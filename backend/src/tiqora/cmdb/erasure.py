"""GDPR erasure of the Tiqora CMDB (called from ``gdpr/erasure.py``).

What an erased customer leaves behind in the CMDB, and what happens to it:

* ``tiqora_itsm_link`` rows with the customer user (or a hard-deleted ticket) as an end are
  deleted. History lines of the affected CIs that name those objects (``LinkAdd``/``LinkDelete``
  details carry the object's label) are redacted; the redaction is per kind, so other
  customers' or tickets' link lines on the same CI are redacted with them. Link lines that
  name the customer (display name) or a hard-deleted ticket (number) are also redacted on every
  other CI, so a link removed before the erasure leaves no name behind either.
* In anonymise mode the customer's tickets stay, but their titles become the placeholder; the
  link history names a ticket as ``<tn> <title>``, so those lines keep only the number
  (``ticket <tn> [gelöscht]``), on every CI, for current and removed links alike.
* ``customer_user`` attribute values holding the login are pseudonymised with the same token the
  tickets get. The current version is replaced by a new system version (change note
  ``Datenschutz``); the previous current version and every older or archived version is
  redacted in place (the product owner's default; there is no "keep" switch).
* CI history lines that contain the login (value changes) get the token.
* The search index of the changed CIs is rebuilt from the new current version.

Every changed or deleted row is handed to ``add_backup`` first, so ``rollback_job`` can restore
it. Rows this module creates are reported with a ``<table>:created`` marker that rollback
deletes again.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

from sqlalchemy import bindparam, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.definition import AttributeDef, parse_definition_json
from tiqora.cmdb.index import write_index
from tiqora.cmdb.values import canonical_json
from tiqora.db.tiqora.models import (
    TiqoraCmdbCi,
    TiqoraCmdbCiHistory,
    TiqoraCmdbCiVersion,
    TiqoraCmdbCiVersionArchive,
    TiqoraCmdbClass,
    TiqoraCmdbClassDefinition,
    TiqoraItsmLink,
)
from tiqora.jobs.service import utc_now

#: ``add_backup(table, pk, original_row)``
BackupFn = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[None]]

CREATED_SUFFIX: Final = ":created"
#: tables whose backup rows hold only the changed columns (restored with an UPDATE by id)
PARTIAL_TABLES: Final = frozenset(
    {
        "tiqora_cmdb_ci",
        "tiqora_cmdb_ci_version",
        "tiqora_cmdb_ci_version_archive",
        "tiqora_cmdb_ci_history",
    }
)
CHANGE_NOTE: Final = "Datenschutz"
REDACTED_LINK: Final = "[gelöscht]"
_CHUNK: Final = 50
#: width of ``tiqora_cmdb_ci_history.detail`` (``cmdb.links`` cuts its lines there)
_DETAIL_MAX: Final = 1000
_SYSTEM_USER: Final = 1


def scrub_values(
    attrs: Sequence[AttributeDef], values: Mapping[str, Any], mapping: Mapping[str, str]
) -> tuple[dict[str, Any], bool]:
    """``values`` with every ``customer_user`` entry found in ``mapping`` replaced; the flag
    says whether anything changed. Keys the definition does not know are carried along."""
    by_key = {a.key: a for a in attrs}
    out: dict[str, Any] = {}
    changed = False
    for key, entries in values.items():
        attr = by_key.get(key)
        if attr is None or not isinstance(entries, list):
            out[key] = entries
            continue
        new_entries: list[Any] = []
        for entry in entries:
            if not isinstance(entry, dict):
                new_entries.append(entry)
                continue
            new = dict(entry)
            value = entry.get("v")
            if attr.type == "customer_user" and isinstance(value, str) and value in mapping:
                new["v"] = mapping[value]
                changed = True
            sub = entry.get("s")
            if attr.sub and isinstance(sub, dict):
                new_sub, sub_changed = scrub_values(attr.sub, sub, mapping)
                if sub_changed:
                    new["s"] = new_sub
                    changed = True
            new_entries.append(new)
        out[key] = new_entries
    return out, changed


def _needle(login: str) -> str:
    """How ``login`` appears inside the stored JSON text."""
    return json.dumps(login, ensure_ascii=False)[1:-1]


async def _definitions(session: AsyncSession, ids: set[int]) -> dict[int, list[AttributeDef]]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(TiqoraCmdbClassDefinition.id, TiqoraCmdbClassDefinition.attributes).where(
                TiqoraCmdbClassDefinition.id.in_(sorted(ids))
            )
        )
    ).all()
    return {int(i): parse_definition_json(str(a)) for i, a in rows}


async def _erase_links(
    session: AsyncSession,
    add_backup: BackupFn,
    logins: Sequence[str],
    ticket_ids: Sequence[int],
) -> tuple[int, set[int], set[str]]:
    """Delete the links; returns (count, affected CI ids, redacted object kinds)."""
    cond = []
    if logins:
        cond.append(
            (TiqoraItsmLink.source_kind == "customer_user")
            & (TiqoraItsmLink.source_key.in_(list(logins)))
        )
        cond.append(
            (TiqoraItsmLink.target_kind == "customer_user")
            & (TiqoraItsmLink.target_key.in_(list(logins)))
        )
    if ticket_ids:
        keys = [str(t) for t in ticket_ids]
        cond.append((TiqoraItsmLink.source_kind == "ticket") & TiqoraItsmLink.source_key.in_(keys))
        cond.append((TiqoraItsmLink.target_kind == "ticket") & TiqoraItsmLink.target_key.in_(keys))
    if not cond:
        return 0, set(), set()
    rows = (
        (await session.execute(select(TiqoraItsmLink.__table__).where(or_(*cond)))).mappings().all()
    )
    cis: set[int] = set()
    kinds: set[str] = set()
    for row in rows:
        await add_backup("tiqora_itsm_link", {"id": int(row["id"])}, dict(row))
        for kind, key in (
            (row["source_kind"], row["source_key"]),
            (row["target_kind"], row["target_key"]),
        ):
            if kind == "ci" and str(key).isdigit():
                cis.add(int(key))
            elif kind in ("customer_user", "ticket"):
                kinds.add(kind)
    ids = [int(r["id"]) for r in rows]
    for i in range(0, len(ids), 1000):
        await session.execute(
            text("DELETE FROM tiqora_itsm_link WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": ids[i : i + 1000]},
        )
    return len(ids), cis, kinds


async def _redact_link_history(
    session: AsyncSession, add_backup: BackupFn, cis: set[int], kinds: set[str]
) -> int:
    if not cis or not kinds:
        return 0
    n = 0
    ci_list = sorted(cis)
    for i in range(0, len(ci_list), 1000):
        rows = (
            await session.execute(
                select(TiqoraCmdbCiHistory.id, TiqoraCmdbCiHistory.detail).where(
                    TiqoraCmdbCiHistory.ci_id.in_(ci_list[i : i + 1000]),
                    TiqoraCmdbCiHistory.event.in_(["LinkAdd", "LinkDelete"]),
                )
            )
        ).all()
        for hid, hdetail in rows:
            detail = str(hdetail)
            prefix, sep, rest = detail.partition(": ")
            kind = rest.partition(" ")[0] if sep else ""
            if kind not in kinds:
                continue
            await add_backup("tiqora_cmdb_ci_history", {"id": int(hid)}, {"detail": detail})
            await session.execute(
                update(TiqoraCmdbCiHistory)
                .where(TiqoraCmdbCiHistory.id == hid)
                .values(detail=f"{prefix}: {kind} {REDACTED_LINK}")
                .execution_options(synchronize_session=False)
            )
            n += 1
    return n


def _names_object(kind: str, rest: str, detail: str, label: str) -> bool:
    """Does the history ``detail`` (``<type>: <kind> <rest>``) name the erased object?"""
    if kind == "ticket":
        # label is ``<tn> <title>``; the ticket number alone identifies it
        return rest == label or rest.startswith(f"{label} ")
    # a label cut at the column width still names the customer
    return rest == label or (len(detail) >= _DETAIL_MAX and label.startswith(rest))


async def _redact_link_history_by_label(
    session: AsyncSession, add_backup: BackupFn, kind: str, labels: Sequence[str]
) -> int:
    """Redact ``LinkAdd``/``LinkDelete`` lines naming an erased object on **every** CI.

    The CI-scoped pass above only sees links that still exist at erasure time; a link removed
    earlier left its history lines behind. The lines are found by the object's label as
    ``cmdb.links`` wrote it (customer display name, ticket number), captured before the
    customer row was anonymised. Another object with the very same label is redacted too.
    """
    n = 0
    for label in sorted({lb for lb in labels if lb.strip()}):
        rows = (
            await session.execute(
                select(TiqoraCmdbCiHistory.id, TiqoraCmdbCiHistory.detail).where(
                    TiqoraCmdbCiHistory.event.in_(["LinkAdd", "LinkDelete"]),
                    TiqoraCmdbCiHistory.detail.contains(f": {kind} {label}", autoescape=True),
                )
            )
        ).all()
        for hid, hdetail in rows:
            detail = str(hdetail)
            prefix, sep, rest_all = detail.partition(": ")
            found_kind, _, rest = rest_all.partition(" ")
            if not sep or found_kind != kind or not _names_object(kind, rest, detail, label):
                continue
            await add_backup("tiqora_cmdb_ci_history", {"id": int(hid)}, {"detail": detail})
            await session.execute(
                update(TiqoraCmdbCiHistory)
                .where(TiqoraCmdbCiHistory.id == hid)
                .values(detail=f"{prefix}: {kind} {REDACTED_LINK}")
                .execution_options(synchronize_session=False)
            )
            n += 1
    return n


async def _redact_ticket_titles(
    session: AsyncSession, add_backup: BackupFn, numbers: Sequence[str]
) -> int:
    """Anonymise mode: ``LinkAdd``/``LinkDelete`` lines naming one of these tickets as
    ``<tn> <title>`` become ``ticket <tn> [gelöscht]`` (the ticket's title is anonymised too).

    One scan over the ticket link lines, matched on the exact number in Python."""
    wanted = {n.strip() for n in numbers if n and n.strip()}
    if not wanted:
        return 0
    rows = (
        await session.execute(
            select(TiqoraCmdbCiHistory.id, TiqoraCmdbCiHistory.detail).where(
                TiqoraCmdbCiHistory.event.in_(["LinkAdd", "LinkDelete"]),
                TiqoraCmdbCiHistory.detail.contains(": ticket ", autoescape=True),
            )
        )
    ).all()
    n = 0
    for hid, hdetail in rows:
        detail = str(hdetail)
        prefix, sep, rest_all = detail.partition(": ")
        kind, _, rest = rest_all.partition(" ")
        tn, has_title, _title = rest.partition(" ")
        if not sep or kind != "ticket" or tn not in wanted or not has_title:
            continue
        redacted = f"{prefix}: ticket {tn} {REDACTED_LINK}"
        if detail == redacted:
            continue
        await add_backup("tiqora_cmdb_ci_history", {"id": int(hid)}, {"detail": detail})
        await session.execute(
            update(TiqoraCmdbCiHistory)
            .where(TiqoraCmdbCiHistory.id == hid)
            .values(detail=redacted)
            .execution_options(synchronize_session=False)
        )
        n += 1
    return n


async def _pseudonymise_versions(
    session: AsyncSession,
    add_backup: BackupFn,
    mapping: Mapping[str, str],
    *,
    archive: bool,
) -> tuple[int, set[int]]:
    """Redact matching versions in place; returns (count, CI ids whose *current* version
    changed). ``archive`` selects the archive table instead of the hot one."""
    model: Any = TiqoraCmdbCiVersionArchive if archive else TiqoraCmdbCiVersion
    table = "tiqora_cmdb_ci_version_archive" if archive else "tiqora_cmdb_ci_version"
    logins = sorted(mapping)
    seen: dict[int, tuple[int, int, str]] = {}
    for i in range(0, len(logins), _CHUNK):
        needles = [_needle(lg) for lg in logins[i : i + _CHUNK]]
        rows = (
            await session.execute(
                select(model.id, model.ci_id, model.definition_id, model.attributes).where(
                    or_(*[model.attributes.contains(n) for n in needles])
                )
            )
        ).all()
        for vid, ci_id, def_id, attrs_json in rows:
            seen[int(vid)] = (int(ci_id), int(def_id), str(attrs_json))
    defs = await _definitions(session, {d for _, d, _ in seen.values()})
    current: dict[int, int | None] = {}
    if not archive and seen:
        ci_ids = sorted({c for c, _, _ in seen.values()})
        for i in range(0, len(ci_ids), 1000):
            for cid, vid in (
                await session.execute(
                    select(TiqoraCmdbCi.id, TiqoraCmdbCi.current_version_id).where(
                        TiqoraCmdbCi.id.in_(ci_ids[i : i + 1000])
                    )
                )
            ).all():
                current[int(cid)] = None if vid is None else int(vid)
    n = 0
    current_changed: set[int] = set()
    for vid, (ci_id, def_id, attrs_json) in sorted(seen.items()):
        try:
            values = json.loads(attrs_json)
        except ValueError:
            continue
        if not isinstance(values, dict):
            continue
        new_values, changed = scrub_values(defs.get(def_id, []), values, mapping)
        if not changed:
            continue
        await add_backup(table, {"id": vid}, {"attributes": attrs_json})
        await session.execute(
            update(model)
            .where(model.id == vid)
            .values(attributes=canonical_json(new_values))
            .execution_options(synchronize_session=False)
        )
        n += 1
        if not archive and current.get(ci_id) == vid:
            current_changed.add(ci_id)
    return n, current_changed


async def _new_current_version(session: AsyncSession, add_backup: BackupFn, ci_id: int) -> bool:
    """Add the ``Datenschutz`` system version (a copy of the already redacted current one)."""
    ci = (
        await session.execute(
            select(TiqoraCmdbCi)
            .where(TiqoraCmdbCi.id == ci_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if ci is None or ci.current_version_id is None:
        return False
    old = (
        await session.execute(
            select(TiqoraCmdbCiVersion)
            .where(TiqoraCmdbCiVersion.id == int(ci.current_version_id))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if old is None:
        return False
    now = utc_now()
    current_def = (
        await session.execute(
            select(TiqoraCmdbClass.current_definition_id).where(
                TiqoraCmdbClass.id == int(ci.class_id)
            )
        )
    ).scalar_one_or_none()
    definition_id = int(current_def) if current_def is not None else int(old.definition_id)
    version = TiqoraCmdbCiVersion(
        ci_id=ci_id,
        definition_id=int(old.definition_id),
        name=old.name,
        deployment_state_id=int(old.deployment_state_id),
        incident_state_id=int(old.incident_state_id),
        attributes=old.attributes,
        change_note=CHANGE_NOTE,
        created_by=_SYSTEM_USER,
        created_at=now,
    )
    session.add(version)
    await session.flush()
    await add_backup(
        "tiqora_cmdb_ci",
        {"id": ci_id},
        {
            "current_version_id": int(ci.current_version_id),
            "version_count": int(ci.version_count),
            "lock_version": int(ci.lock_version),
            "changed_by": int(ci.changed_by),
            "changed_at": ci.changed_at,
        },
    )
    await add_backup(f"tiqora_cmdb_ci_version{CREATED_SUFFIX}", {"id": int(version.id)}, {})
    count = int(ci.version_count) + 1
    await session.execute(
        update(TiqoraCmdbCi)
        .where(TiqoraCmdbCi.id == ci_id)
        .values(
            current_version_id=int(version.id),
            version_count=count,
            lock_version=TiqoraCmdbCi.lock_version + 1,
            changed_by=_SYSTEM_USER,
            changed_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    history = TiqoraCmdbCiHistory(
        ci_id=ci_id,
        event="VersionCreate",
        detail=str(count),
        version_id=int(version.id),
        created_by=_SYSTEM_USER,
        created_at=now,
    )
    session.add(history)
    await session.flush()
    await add_backup(f"tiqora_cmdb_ci_history{CREATED_SUFFIX}", {"id": int(history.id)}, {})
    attrs = (await _definitions(session, {definition_id})).get(definition_id, [])
    await write_index(session, ci_id, attrs, json.loads(str(old.attributes)))
    return True


async def _pseudonymise_history(
    session: AsyncSession, add_backup: BackupFn, mapping: Mapping[str, str]
) -> int:
    n = 0
    for login, token in sorted(mapping.items()):
        rows = (
            await session.execute(
                select(TiqoraCmdbCiHistory.id, TiqoraCmdbCiHistory.detail).where(
                    TiqoraCmdbCiHistory.detail.contains(login, autoescape=True)
                )
            )
        ).all()
        for hid, hdetail in rows:
            detail = str(hdetail)
            new = detail.replace(login, token)
            if new == detail:
                continue
            await add_backup("tiqora_cmdb_ci_history", {"id": int(hid)}, {"detail": detail})
            await session.execute(
                update(TiqoraCmdbCiHistory)
                .where(TiqoraCmdbCiHistory.id == hid)
                .values(detail=new)
                .execution_options(synchronize_session=False)
            )
            n += 1
    return n


async def erase_cmdb_customer_data(
    session: AsyncSession,
    *,
    replacements: Mapping[str, str],
    deleted_ticket_ids: Sequence[int],
    add_backup: BackupFn,
    customer_labels: Sequence[str] = (),
    deleted_ticket_numbers: Sequence[str] = (),
    anonymised_ticket_numbers: Sequence[str] = (),
) -> dict[str, int]:
    """Apply the erasure to the CMDB tables (see the module docstring); returns the counts.

    ``replacements`` maps each erased login to the token the tickets get; the caller commits.
    ``customer_labels`` are the erased customers' display names as they were before the
    anonymisation, ``deleted_ticket_numbers`` the numbers of the hard-deleted tickets: link
    history naming them is redacted on every CI, also where the link is long gone.
    ``anonymised_ticket_numbers`` are the customer's tickets that stay with an anonymised title
    (anonymise mode): their link history keeps the number and loses the title.
    """
    counts: dict[str, int] = {}
    logins = sorted(replacements)
    n_links, link_cis, kinds = await _erase_links(
        session, add_backup, logins, list(deleted_ticket_ids)
    )
    counts["tiqora_itsm_link"] = n_links
    counts["tiqora_cmdb_ci_history"] = await _redact_link_history(
        session, add_backup, link_cis, kinds
    )
    counts["tiqora_cmdb_ci_history"] += await _redact_link_history_by_label(
        session, add_backup, "customer_user", customer_labels
    )
    counts["tiqora_cmdb_ci_history"] += await _redact_link_history_by_label(
        session, add_backup, "ticket", deleted_ticket_numbers
    )
    counts["tiqora_cmdb_ci_history"] += await _redact_ticket_titles(
        session, add_backup, anonymised_ticket_numbers
    )
    if logins:
        n_hot, current_cis = await _pseudonymise_versions(
            session, add_backup, replacements, archive=False
        )
        n_arch, _ = await _pseudonymise_versions(session, add_backup, replacements, archive=True)
        counts["tiqora_cmdb_ci_version"] = n_hot
        counts["tiqora_cmdb_ci_version_archive"] = n_arch
        for ci_id in sorted(current_cis):
            await _new_current_version(session, add_backup, ci_id)
        counts["cmdb_ci_new_version"] = len(current_cis)
        counts["tiqora_cmdb_ci_history"] += await _pseudonymise_history(
            session, add_backup, replacements
        )
    return {k: v for k, v in counts.items() if v}


__all__ = [
    "CHANGE_NOTE",
    "CREATED_SUFFIX",
    "PARTIAL_TABLES",
    "BackupFn",
    "erase_cmdb_customer_data",
    "scrub_values",
]
