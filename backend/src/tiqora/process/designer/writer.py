"""The only writer of ``pm_*`` and ``pm_entity_sync`` (Znuny draft semantics).

Every method mirrors the SQL of Znuny's ``Kernel::System::ProcessManagement::DB::*``
row for row: the same columns, ``current_timestamp`` for the time columns, the same
``pm_entity_sync`` select-then-insert/update (``DB/Entity.pm:126-200``), the same
"no change, no write" early return (``DB/Activity.pm:480-510``, ``DB/Process.pm:660-690``)
and the same ``ProcessManagement_<Type>`` cache clean-up, queued here as a
``tiqora_cache_invalidation`` signal for the TiqoraSync add-on.

A write is a *draft*: the row changes and ``pm_entity_sync`` says ``not_sync``. The engine
keeps running the newest deployed revision until somebody deploys. All methods flush
only; the caller commits. A writer instance belongs to one request: it queues each cache
signal type once.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.process_management import (
    PmActivity,
    PmActivityDialog,
    PmProcess,
    PmTransition,
    PmTransitionAction,
)
from tiqora.process.config import _load_yaml_mapping
from tiqora.process.designer.ids import (
    ENTITY_ID_RE,
    EntityType,
    SubEntityType,
    generate_entity_id,
)
from tiqora.process.designer.versioning import entity_version
from tiqora.process.designer.yaml_codec import dump_znuny_yaml, stringify_scalars
from tiqora.znuny.cache_invalidation import invalidate_cache_type

_MODELS: dict[str, Any] = {
    "Process": PmProcess,
    "Activity": PmActivity,
    "ActivityDialog": PmActivityDialog,
    "Transition": PmTransition,
    "TransitionAction": PmTransitionAction,
}
_TABLES = {
    "Process": "pm_process",
    "Activity": "pm_activity",
    "ActivityDialog": "pm_activity_dialog",
    "Transition": "pm_transition",
    "TransitionAction": "pm_transition_action",
}
_PROCESS_STATES = ("S1", "S2", "S3")  # Process/State.pm:48-50
_INTERFACES = ("AgentInterface", "CustomerInterface")
_CONDITION_LINKING = ("and", "or", "xor")
_NAME_MAX = 200  # pm_*.name is String(200)
COPY_SUFFIX = " (Kopie)"
SYNC_NOT_SYNC = "not_sync"
SYNC_DELETED = "deleted"


class EntityNotFound(LookupError):
    """No ``pm_*`` row with this entity id."""


class VersionConflict(Exception):
    """``expected_version`` does not match the stored row (409 "inzwischen geändert")."""


class EntityInUse(Exception):
    """A still referenced entity cannot be deleted (409)."""

    def __init__(self, used_by: list[str]) -> None:
        super().__init__("Wird noch verwendet von: " + ", ".join(used_by))
        self.used_by = used_by


class ProcessNotInactive(Exception):
    """Only Inactive processes can be deleted (``AdminProcessManagement.pm:1882-1990``)."""


class InvalidReference(ValueError):
    """Unknown or malformed entity id, or a config Znuny would refuse (422)."""


@dataclass(frozen=True, slots=True)
class WrittenEntity:
    entity_type: EntityType
    entity_id: str
    id: int
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class _Row:
    id: int
    entity_id: str
    name: str
    config: str
    layout: str = ""
    state_entity_id: str = ""

    @property
    def version(self) -> str:
        return entity_version(
            name=self.name,
            config=self.config,
            layout=self.layout,
            state_entity_id=self.state_entity_id,
        )


def _text(value: object) -> str:
    if isinstance(value, memoryview | bytearray | bytes):
        return bytes(value).decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _check_name(name: str) -> str:
    if not name or not name.strip():
        raise InvalidReference("Der Name darf nicht leer sein.")
    if len(name) > _NAME_MAX:
        raise InvalidReference(f"Der Name darf höchstens {_NAME_MAX} Zeichen lang sein.")
    return name


def _renumber(value: object) -> dict[str, Any]:
    """Keys "1".."n" in numeric order (Znuny's dialog and condition lists)."""
    if isinstance(value, Mapping):

        def order(item: tuple[Any, Any]) -> tuple[int, int, str]:
            key = str(item[0])
            return (0, int(key), key) if key.isdigit() else (1, 0, key)

        values = [v for _, v in sorted(value.items(), key=order)]
    elif isinstance(value, list | tuple):
        values = list(value)
    else:
        values = []
    kept = [v for v in values if v not in (None, "", {})]
    return {str(i): v for i, v in enumerate(kept, start=1)}


def _scope(config: dict[str, Any]) -> None:
    """``DB/Activity.pm:133-139`` (all four sub-entity types)."""
    scope = config.get("Scope")
    config["Scope"] = "Global" if scope is None else scope
    if config["Scope"] != "Global" and not config.get("ScopeEntityID"):
        raise InvalidReference("Für einen eingeschränkten Geltungsbereich fehlt ScopeEntityID.")


def normalise_config(entity_type: SubEntityType, config: Mapping[str, Any]) -> dict[str, Any]:
    """Shape a sub-entity config like Znuny's own forms store it (stringified scalars)."""
    if not isinstance(config, Mapping):
        raise InvalidReference("Die Konfiguration muss ein Objekt sein.")
    cfg = stringify_scalars(_mapping(config))
    _scope(cfg)
    if entity_type == "Activity":
        cfg["ActivityDialog"] = _renumber(cfg.get("ActivityDialog"))
    elif entity_type == "ActivityDialog":
        fields = _mapping(cfg.get("Fields"))
        order = cfg.get("FieldOrder")
        wanted = [str(f) for f in order] if isinstance(order, list) else []
        ordered = [f for f in dict.fromkeys(wanted) if f in fields]
        ordered += sorted(f for f in fields if f not in ordered)
        interface = cfg.get("Interface")
        names = [interface] if isinstance(interface, str) else interface
        names = [n for n in dict.fromkeys(names or []) if n]
        unknown = [n for n in names if n not in _INTERFACES]
        if unknown:
            raise InvalidReference(f"Unbekannte Oberfläche: {', '.join(unknown)}")
        cfg["Fields"] = fields
        cfg["FieldOrder"] = ordered
        cfg["Interface"] = names or [_INTERFACES[0]]
    elif entity_type == "Transition":
        cfg["Condition"] = _renumber(cfg.get("Condition"))
        linking = cfg.get("ConditionLinking") or "and"
        if linking not in _CONDITION_LINKING:
            raise InvalidReference("ConditionLinking muss and, or oder xor sein.")
        cfg["ConditionLinking"] = linking
    else:  # TransitionAction, DB/TransitionAction.pm:133-157
        module = cfg.get("Module")
        if not isinstance(module, str) or "::" not in module:
            raise InvalidReference("Das Modul der Aktion muss ein Perl-Modulpfad sein.")
        cfg["Config"] = _mapping(cfg.get("Config"))
    return cfg


class ProcessWriter:
    def __init__(self, session: AsyncSession, *, user_id: int) -> None:
        self._session = session
        self._user_id = user_id
        self._signalled: set[str] = set()

    # -- low level -----------------------------------------------------------------

    async def _row(
        self, entity_type: EntityType, entity_id: str, *, lock: bool = False
    ) -> _Row | None:
        model = _MODELS[entity_type]
        cols = [model.id, model.entity_id, model.name, model.config]
        if entity_type == "Process":
            cols += [model.layout, model.state_entity_id]
        stmt = select(*cols).where(model.entity_id == entity_id)
        if lock:
            stmt = stmt.with_for_update()  # both dialects; serialises concurrent editors
        found = (await self._session.execute(stmt)).first()
        if found is None:
            return None
        return _Row(
            id=int(found[0]),
            entity_id=found[1],
            name=found[2],
            config=_text(found[3]),
            layout=_text(found[4]) if entity_type == "Process" else "",
            state_entity_id=_text(found[5]) if entity_type == "Process" else "",
        )

    async def _require(
        self, entity_type: EntityType, entity_id: str, *, lock: bool = False
    ) -> _Row:
        row = await self._row(entity_type, entity_id, lock=lock)
        if row is None:
            raise EntityNotFound(entity_id)
        return row

    async def _mark_sync(self, entity_type: EntityType, entity_id: str, state: str) -> None:
        """``DB/Entity.pm:126-200``: select, then insert or update."""
        params = {"t": entity_type, "e": entity_id, "s": state}
        existing = (
            await self._session.execute(
                text("SELECT 1 FROM pm_entity_sync WHERE entity_type = :t AND entity_id = :e"),
                params,
            )
        ).first()
        if existing is None:
            await self._session.execute(
                text(
                    "INSERT INTO pm_entity_sync"
                    " (entity_type, entity_id, sync_state, create_time, change_time)"
                    " VALUES (:t, :e, :s, current_timestamp, current_timestamp)"
                ),
                params,
            )
        else:
            await self._session.execute(
                text(
                    "UPDATE pm_entity_sync SET sync_state = :s, change_time = current_timestamp"
                    " WHERE entity_type = :t AND entity_id = :e"
                ),
                params,
            )

    async def _signal(self, entity_type: EntityType) -> None:
        cache_type = f"ProcessManagement_{entity_type}"
        if cache_type in self._signalled:
            return
        self._signalled.add(cache_type)
        await invalidate_cache_type(self._session, cache_type)

    async def _after_write(self, entity_type: EntityType, entity_id: str, state: str) -> None:
        await self._mark_sync(entity_type, entity_id, state)
        await self._signal(entity_type)

    def _written(self, entity_type: EntityType, row: _Row) -> WrittenEntity:
        return WrittenEntity(entity_type, row.entity_id, row.id, row.name, row.version)

    async def _insert(
        self,
        entity_type: EntityType,
        entity_id: str,
        *,
        name: str,
        config: str,
        layout: str = "",
        state_entity_id: str = "",
    ) -> WrittenEntity:
        table = _TABLES[entity_type]
        params: dict[str, Any] = {
            "e": entity_id,
            "n": name,
            "c": config,
            "u": self._user_id,
        }
        if entity_type == "Process":  # DB/Process.pm:156-168
            sql = (
                "INSERT INTO pm_process (entity_id, name, state_entity_id, layout, config,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:e, :n, :s, :l, :c, current_timestamp, :u, current_timestamp, :u)"
            )
            params |= {"s": state_entity_id, "l": layout}
        else:  # DB/Activity.pm:140-170
            sql = (
                f"INSERT INTO {table} (entity_id, name, config, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:e, :n, :c, current_timestamp, :u, current_timestamp, :u)"
            )
        await self._session.execute(text(sql), params)
        new_id = (
            await self._session.execute(
                text(f"SELECT id FROM {table} WHERE entity_id = :e"), {"e": entity_id}
            )
        ).scalar_one()
        await self._after_write(entity_type, entity_id, SYNC_NOT_SYNC)
        row = _Row(int(new_id), entity_id, name, config, layout, state_entity_id)
        return self._written(entity_type, row)

    async def _update(
        self,
        entity_type: EntityType,
        row: _Row,
        *,
        name: str,
        config: str,
        layout: str = "",
        state_entity_id: str = "",
    ) -> WrittenEntity:
        table = _TABLES[entity_type]
        params: dict[str, Any] = {"i": row.id, "n": name, "c": config, "u": self._user_id}
        if entity_type == "Process":  # DB/Process.pm:693-706
            sql = (
                "UPDATE pm_process SET name = :n, state_entity_id = :s, layout = :l, config = :c,"
                " change_time = current_timestamp, change_by = :u WHERE id = :i"
            )
            params |= {"s": state_entity_id, "l": layout}
        else:  # DB/Activity.pm:497-511
            sql = (
                f"UPDATE {table} SET name = :n, config = :c,"
                " change_time = current_timestamp, change_by = :u WHERE id = :i"
            )
        await self._session.execute(text(sql), params)
        await self._after_write(entity_type, row.entity_id, SYNC_NOT_SYNC)
        return self._written(
            entity_type,
            _Row(row.id, row.entity_id, name, config, layout, state_entity_id),
        )

    async def _delete(self, entity_type: EntityType, row: _Row) -> None:
        await self._session.execute(
            text(f"DELETE FROM {_TABLES[entity_type]} WHERE id = :i"), {"i": row.id}
        )
        await self._after_write(entity_type, row.entity_id, SYNC_DELETED)

    # -- sub entities (Activity, ActivityDialog, Transition, TransitionAction) --------

    async def create_entity(
        self, entity_type: SubEntityType, *, name: str, config: Mapping[str, Any]
    ) -> WrittenEntity:
        cfg = normalise_config(entity_type, config)
        return await self._insert(
            entity_type,
            generate_entity_id(entity_type),
            name=_check_name(name),
            config=dump_znuny_yaml(cfg),
        )

    async def update_entity(
        self,
        entity_type: SubEntityType,
        entity_id: str,
        *,
        expected_version: str,
        name: str,
        config: Mapping[str, Any],
    ) -> WrittenEntity:
        row = await self._require(entity_type, entity_id, lock=True)
        if row.version != expected_version:
            raise VersionConflict(entity_id)
        cfg = normalise_config(entity_type, config)
        _check_name(name)
        if name == row.name and stringify_scalars(_load_yaml_mapping(row.config)) == cfg:
            return self._written(entity_type, row)  # DB/Activity.pm:480-486: no write at all
        return await self._update(entity_type, row, name=name, config=dump_znuny_yaml(cfg))

    async def delete_entity(self, entity_type: SubEntityType, entity_id: str) -> None:
        row = await self._require(entity_type, entity_id, lock=True)
        used_by = await self.entity_usage(entity_type, entity_id)
        if used_by:
            raise EntityInUse(used_by)
        await self._delete(entity_type, row)

    async def entity_usage(self, entity_type: EntityType, entity_id: str) -> list[str]:
        """Entity ids that reference this one (``AdminProcessManagement.pm:1925-2030``).

        Activities, transitions and transition actions are referenced by the ``Path`` of
        processes; dialogs by activities. Nothing references a process (tickets keep
        running on the deployed revision), so its usage is always empty.
        """
        if entity_type == "Process":
            return []
        if entity_type == "ActivityDialog":
            rows = await self._configs("Activity")
            return sorted(
                eid
                for eid, cfg in rows.items()
                if entity_id in _mapping(cfg.get("ActivityDialog")).values()
            )
        users: list[str] = []
        for eid, cfg in (await self._configs("Process")).items():
            path = _mapping(cfg.get("Path"))
            if entity_type == "Activity":
                hit = entity_id in path
            elif entity_type == "Transition":
                hit = any(entity_id in _mapping(trs) for trs in path.values())
            else:
                hit = any(
                    entity_id in (_mapping(tr).get("TransitionAction") or [])
                    for trs in path.values()
                    for tr in _mapping(trs).values()
                )
            if hit:
                users.append(eid)
        return sorted(users)

    async def _configs(self, entity_type: EntityType) -> dict[str, dict[str, Any]]:
        model = _MODELS[entity_type]
        rows = await self._session.execute(select(model.entity_id, model.config))
        return {eid: _load_yaml_mapping(_text(cfg)) for eid, cfg in rows.all()}

    # -- processes ---------------------------------------------------------------------

    async def create_process(
        self, *, name: str, description: str = "", state_entity_id: str = "S2"
    ) -> WrittenEntity:
        if state_entity_id not in _PROCESS_STATES:
            raise InvalidReference(f"Unbekannter Prozessstatus: {state_entity_id}")
        config = {
            "Description": description,
            "Path": {},
            "StartActivity": "",
            "StartActivityDialog": "",
        }
        return await self._insert(
            "Process",
            generate_entity_id("Process"),
            name=_check_name(name),
            config=dump_znuny_yaml(config),
            layout=dump_znuny_yaml({}),
            state_entity_id=state_entity_id,
        )

    async def update_process(
        self,
        entity_id: str,
        *,
        expected_version: str,
        name: str,
        description: str,
        state_entity_id: str,
        start_activity: str,
        start_activity_dialog: str,
        path: Mapping[str, Mapping[str, Mapping[str, Any]]],
        layout: Mapping[str, Mapping[str, Any]],
    ) -> WrittenEntity:
        row = await self._require("Process", entity_id, lock=True)
        if row.version != expected_version:
            raise VersionConflict(entity_id)
        _check_name(name)
        if state_entity_id not in _PROCESS_STATES:
            raise InvalidReference(f"Unbekannter Prozessstatus: {state_entity_id}")
        path_cfg = await self._checked_path(path, start_activity, start_activity_dialog)
        config = stringify_scalars(
            {
                "Description": description,
                "Path": path_cfg,
                "StartActivity": start_activity,
                "StartActivityDialog": start_activity_dialog,
            }
        )
        layout_cfg = stringify_scalars(_mapping(layout))
        if (
            name == row.name
            and state_entity_id == row.state_entity_id
            and stringify_scalars(_load_yaml_mapping(row.config)) == config
            and stringify_scalars(_load_yaml_mapping(row.layout)) == layout_cfg
        ):
            return self._written("Process", row)  # DB/Process.pm:660-690
        return await self._update(
            "Process",
            row,
            name=name,
            config=dump_znuny_yaml(config),
            layout=dump_znuny_yaml(layout_cfg),
            state_entity_id=state_entity_id,
        )

    async def _checked_path(
        self,
        path: Mapping[str, Mapping[str, Mapping[str, Any]]],
        start_activity: str,
        start_activity_dialog: str,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Every id in the path exists (loaded with IN lists, never joined)."""
        clean: dict[str, dict[str, dict[str, Any]]] = {}
        activities: set[str] = set()
        transitions: set[str] = set()
        actions: set[str] = set()
        for act, trs in _mapping(path).items():
            clean[act] = {}
            activities.add(act)
            for tr, spec in _mapping(trs).items():
                transitions.add(tr)
                spec_m = _mapping(spec)
                target = spec_m.get("ActivityEntityID")
                if target:
                    activities.add(str(target))
                listed = spec_m.get("TransitionAction") or []
                if not isinstance(listed, list | tuple):
                    raise InvalidReference("TransitionAction muss eine Liste sein.")
                actions.update(str(a) for a in listed)
                clean[act][tr] = spec_m
        if start_activity:
            activities.add(start_activity)
            if start_activity not in clean:
                raise InvalidReference("Die Startaktivität muss im Pfad vorkommen.")
        for kind, ids in (
            ("Activity", activities),
            ("Transition", transitions),
            ("TransitionAction", actions),
        ):
            await self._assert_exist(kind, ids)  # type: ignore[arg-type]
        if start_activity_dialog:
            if not start_activity:
                raise InvalidReference("Ein Startformular braucht eine Startaktivität.")
            act_row = await self._require("Activity", start_activity)
            dialogs = _mapping(_load_yaml_mapping(act_row.config).get("ActivityDialog"))
            if start_activity_dialog not in dialogs.values():
                raise InvalidReference("Das Startformular gehört nicht zur Startaktivität.")
        return clean

    async def _assert_exist(self, entity_type: EntityType, ids: Iterable[str]) -> None:
        wanted = set(ids)
        if not wanted:
            return
        bad = sorted(i for i in wanted if not ENTITY_ID_RE.match(i))
        if bad:
            raise InvalidReference(f"Ungültige Kennung: {', '.join(bad)}")
        model = _MODELS[entity_type]
        found = set(
            (
                await self._session.execute(
                    select(model.entity_id).where(model.entity_id.in_(wanted))
                )
            )
            .scalars()
            .all()
        )
        missing = sorted(wanted - found)
        if missing:
            raise InvalidReference(f"Unbekannte Einträge: {', '.join(missing)}")

    async def copy_process(self, entity_id: str) -> WrittenEntity:
        """``AdminProcessManagement.pm:714-800``: same layout and config, Inactive, new id."""
        src = await self._require("Process", entity_id)
        name = src.name[: _NAME_MAX - len(COPY_SUFFIX)] + COPY_SUFFIX
        return await self._insert(
            "Process",
            generate_entity_id("Process"),
            name=name,
            config=src.config,
            layout=src.layout,
            state_entity_id="S2",
        )

    async def delete_process(self, entity_id: str) -> None:
        row = await self._require("Process", entity_id, lock=True)
        if row.state_entity_id != "S2":
            raise ProcessNotInactive(entity_id)
        await self._delete("Process", row)
