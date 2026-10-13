"""Znuny's general catalog (read-only): CI classes, deployment and incident states.

Entries are ``general_catalog`` rows; their preferences (``Functionality`` of a state, the
``Permission`` group of a CI class) live in ``general_catalog_preferences`` and are addressed by
``(general_catalog_id, pref_key)`` only: the 7.x table has an extra ``id`` column, the 6.x table
has none, and the same SQL has to serve both (``docs/itsm-import.md``).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.ci_provider import CatalogValue
from tiqora.itsm.znuny_adapter.profiles import PREF_FUNCTIONALITY, PREF_PERMISSION, SchemaProfile

CHUNK = 500  # IN-list size


def chunks[T](items: Sequence[T], size: int = CHUNK) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def _preferences(
    session: AsyncSession, pref_key: str, ids: Collection[int]
) -> dict[int, str | None]:
    out: dict[int, str | None] = {}
    ordered = sorted(ids)
    stmt = text(
        "SELECT general_catalog_id, pref_value FROM general_catalog_preferences"
        " WHERE pref_key = :k AND general_catalog_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    for part in chunks(ordered):
        rows = await session.execute(stmt, {"k": pref_key, "ids": list(part)})
        for catalog_id, value in rows.all():
            out.setdefault(int(catalog_id), None if value is None else str(value))
    return out


async def load_catalog(
    session: AsyncSession, profile: SchemaProfile, role: str
) -> list[CatalogValue]:
    """The valid entries of the catalog class behind ``role``, ordered by name.

    ``functionality`` is the ``Functionality`` preference (``operational|warning|incident`` for
    incident states, ``productive|preproductive|postproductive`` for deployment states).
    """
    catalog_class = profile.catalog_classes[role]
    rows = (
        await session.execute(
            text(
                "SELECT id, name FROM general_catalog"
                " WHERE general_catalog_class = :c AND valid_id = 1 ORDER BY name, id"
            ),
            {"c": catalog_class},
        )
    ).all()
    functionality = await _preferences(session, PREF_FUNCTIONALITY, [int(r[0]) for r in rows])
    return [CatalogValue(str(r[0]), str(r[1]), functionality.get(int(r[0]))) for r in rows]


async def catalog_values_by_id(
    session: AsyncSession, ids: Collection[int]
) -> dict[int, CatalogValue]:
    """Entries by id whatever their validity (a CI keeps a state that was invalidated later)."""
    out: dict[int, CatalogValue] = {}
    ordered = sorted(set(ids))
    stmt = text("SELECT id, name FROM general_catalog WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    names: dict[int, str] = {}
    for part in chunks(ordered):
        for catalog_id, name in (await session.execute(stmt, {"ids": list(part)})).all():
            names[int(catalog_id)] = str(name)
    functionality = await _preferences(session, PREF_FUNCTIONALITY, names)
    for catalog_id, name in names.items():
        out[catalog_id] = CatalogValue(str(catalog_id), name, functionality.get(catalog_id))
    return out


async def class_names(session: AsyncSession, profile: SchemaProfile) -> dict[int, str]:
    """Valid CI classes: ``general_catalog.id`` -> name."""
    return {int(v.key): v.name for v in await load_catalog(session, profile, "ci_class")}


async def class_permission_groups(
    session: AsyncSession, profile: SchemaProfile
) -> dict[int, int | None]:
    """Valid CI class id -> id of the group whose members may see its CIs.

    Znuny's ``ItemClassGroupCheck`` reads the class preference ``Permission``. ``None`` when a
    class has none (or a value that is not a group id): Znuny then grants nobody access.
    """
    classes = await class_names(session, profile)
    prefs = await _preferences(session, PREF_PERMISSION, classes)
    out: dict[int, int | None] = {}
    for class_id in classes:
        raw = prefs.get(class_id)
        out[class_id] = int(raw) if raw is not None and raw.strip().isdigit() else None
    return out
