"""GDPR: rewrite erased customer identities inside saved searches and saved reports.

A saved search (``criteria``) or report (``definition.filter`` and the runtime
``parameters``) can name a customer by login (``customer_user_id`` / ``customer_user``)
or company id (``customer_id`` / ``customer``). After an erasure those values would
survive in tiqora-owned tables, so :func:`scrub_saved_criteria` rewrites them to the
values the tickets received. It runs inside the erasure transaction (the caller commits
or rolls back) and hands every original value to an optional backup callback so that a
rollback of the erasure job restores it.

Only exact (case-insensitive) matches on the identity fields are replaced; free-text
conditions (``title contains ...``), names and descriptions are not touched.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Collection, Mapping
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import (
    TiqoraReport,
    TiqoraReportSchedule,
    TiqoraReportScheduleItem,
    TiqoraReportScheduleRecipient,
    TiqoraSavedSearch,
)
from tiqora.extensions.core import get_registry
from tiqora.reporting.catalogue import BUILTIN_REPORTS, is_catalogue_key

logger = structlog.get_logger(__name__)

#: Condition fields holding a customer login / company id, per kind of saved object.
SEARCH_LOGIN_FIELDS: Final = frozenset({"customer_user_id"})
SEARCH_COMPANY_FIELDS: Final = frozenset({"customer_id"})
REPORT_LOGIN_FIELDS: Final = frozenset({"customer_user"})
REPORT_COMPANY_FIELDS: Final = frozenset({"customer"})

#: Tables whose rows are backed up here (``rollback_job`` restores them column-wise).
SAVED_CRITERIA_TABLES: Final = frozenset({"tiqora_saved_search", "tiqora_report"})
#: Schedule items are backed up as ``{"overrides": <json>}`` under their composite key
#: ``{"schedule_id", "position"}``; ``rollback_job`` has a dedicated branch for them.
SCHEDULE_ITEM_TABLE: Final = "tiqora_report_schedule_item"

_BATCH = 200

BackupFn = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[None]]


def _replace_value(value: Any, lowered: Mapping[str, str]) -> tuple[Any, bool]:
    if isinstance(value, str):
        new = lowered.get(value.casefold())
        return (new, True) if new is not None and new != value else (value, False)
    if isinstance(value, list):
        out = [_replace_value(v, lowered) for v in value]
        return [v for v, _ in out], any(c for _, c in out)
    return value, False


def replace_filter_values(
    where: Any, fields: frozenset[str], mapping: Mapping[str, str]
) -> tuple[Any, bool]:
    """Return ``(new_tree, changed)`` with the values of conditions on ``fields`` mapped.

    Walks ``all`` / ``any`` / ``not`` nodes of a raw (JSON) filter tree; string values and the
    strings inside ``in``-style lists are compared case-insensitively. The input is not mutated.
    """
    lowered = {k.casefold(): v for k, v in mapping.items()}
    if where is None or not lowered:
        return where, False
    return _walk(where, fields, lowered)


def _walk(node: Any, fields: frozenset[str], lowered: Mapping[str, str]) -> tuple[Any, bool]:
    if isinstance(node, list):
        out = [_walk(n, fields, lowered) for n in node]
        return [n for n, _ in out], any(c for _, c in out)
    if not isinstance(node, dict):
        return node, False
    changed = False
    new: dict[str, Any] = {}
    for key, child in node.items():
        if key in ("all", "any", "not") and isinstance(child, (dict, list)):
            new[key], c = _walk(child, fields, lowered)
            changed = changed or c
        else:
            new[key] = child
    if node.get("field") in fields and "value" in node:
        new["value"], c = _replace_value(node["value"], lowered)
        changed = changed or c
    return new, changed


def _scrub_search(raw: str, logins: Mapping[str, str], companies: Mapping[str, str]) -> str | None:
    try:
        crit = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(crit, dict) or "where" not in crit:
        return None
    where, c1 = replace_filter_values(crit["where"], SEARCH_LOGIN_FIELDS, logins)
    where, c2 = replace_filter_values(where, SEARCH_COMPANY_FIELDS, companies)
    if not (c1 or c2):
        return None
    return json.dumps({**crit, "where": where}, sort_keys=True)


def _scrub_report(raw: str, logins: Mapping[str, str], companies: Mapping[str, str]) -> str | None:
    try:
        defn = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(defn, dict):
        return None
    changed = False
    flt = defn.get("filter")
    if isinstance(flt, dict) and "where" in flt:
        where, c1 = replace_filter_values(flt["where"], REPORT_LOGIN_FIELDS, logins)
        where, c2 = replace_filter_values(where, REPORT_COMPANY_FIELDS, companies)
        if c1 or c2:
            defn["filter"] = {**flt, "where": where}
            changed = True
    params = defn.get("parameters")
    if isinstance(params, list):
        for p in params:
            if not isinstance(p, dict):
                continue
            field = p.get("field")
            mapping = (
                logins
                if field in REPORT_LOGIN_FIELDS
                else companies
                if field in REPORT_COMPANY_FIELDS
                else None
            )
            if not mapping:
                continue
            lowered = {k.casefold(): v for k, v in mapping.items()}
            for key in ("allowed_values", "default"):
                if key in p:
                    p[key], c = _replace_value(p[key], lowered)
                    changed = changed or c
    return json.dumps(defn, sort_keys=True) if changed else None


async def scrub_saved_criteria(
    session: AsyncSession,
    login_to_new: Mapping[str, str],
    company_to_new: Mapping[str, str] | None = None,
    *,
    backup: BackupFn | None = None,
) -> int:
    """Rewrite erased logins (and company ids) in all saved searches and reports.

    Includes soft-deleted reports. Rows are locked (``FOR UPDATE``) while they are read, so a
    concurrent edit cannot write the old identity back. ``version`` and ``changed_at`` are bumped
    on every changed row. Does not commit. Returns the number of rows changed.
    """
    logins = {k: v for k, v in login_to_new.items() if k}
    companies = {k: v for k, v in (company_to_new or {}).items() if k}
    if not logins and not companies:
        return 0
    now = datetime.now(UTC).replace(tzinfo=None)
    changed = 0
    for model, column, scrub, table in (
        (TiqoraSavedSearch, "criteria", _scrub_search, "tiqora_saved_search"),
        (TiqoraReport, "definition", _scrub_report, "tiqora_report"),
    ):
        col = getattr(model, column)
        last = 0
        while True:
            rows = (
                await session.execute(
                    select(model.id, col, model.version)
                    .where(model.id > last)
                    .order_by(model.id)
                    .limit(_BATCH)
                    .with_for_update()
                )
            ).all()
            if not rows:
                break
            last = int(rows[-1][0])
            for row_id, raw, version in rows:
                new_raw = scrub(raw, logins, companies)
                if new_raw is None:
                    continue
                if backup is not None:
                    await backup(
                        table,
                        {"id": int(row_id)},
                        {column: raw, "version": int(version)},
                    )
                await session.execute(
                    update(model)
                    .where(model.id == row_id)
                    .values({column: new_raw, "version": int(version) + 1, "changed_at": now})
                )
                changed += 1
    return changed


def _scrub_overrides(
    raw: str,
    param_fields: Mapping[str, str],
    logins: Mapping[str, str],
    companies: Mapping[str, str],
) -> str | None:
    """Rewrite ``parameters`` values whose report parameter targets a customer login / company."""
    try:
        overrides = json.loads(raw)
    except ValueError:
        return None
    params = overrides.get("parameters") if isinstance(overrides, dict) else None
    if not isinstance(params, dict):
        return None
    changed = False
    for key, value in list(params.items()):
        field = param_fields.get(key)
        mapping = (
            logins
            if field in REPORT_LOGIN_FIELDS
            else companies
            if field in REPORT_COMPANY_FIELDS
            else None
        )
        if not mapping:
            continue
        lowered = {k.casefold(): v for k, v in mapping.items()}
        new, c = _replace_value(value, lowered)
        if c:
            params[key] = new
            changed = True
    return json.dumps(overrides, sort_keys=True) if changed else None


async def _parameter_fields(session: AsyncSession, report_key: str) -> dict[str, str]:
    """``{parameter key: report field}`` of a built-in or saved report (soft-deleted included)."""
    if is_catalogue_key(report_key):
        # Unfiltered: erasure must not depend on plugin health (a plugin that failed in this
        # process still has its definition registered).
        builtin = get_registry().get(BUILTIN_REPORTS, report_key)
        if builtin is None:
            # a plugin report whose plugin is not installed: its overrides cannot be scrubbed
            logger.warning("gdpr_report_parameters_unknown", report=report_key)
            return {}
        return {p.key: p.field for p in builtin.definition.parameters}
    if not report_key.isdigit():
        return {}
    raw = (
        await session.execute(
            select(TiqoraReport.definition).where(TiqoraReport.id == int(report_key))
        )
    ).scalar_one_or_none()
    if raw is None:
        return {}
    try:
        defn = json.loads(raw)
    except ValueError:
        return {}
    params = defn.get("parameters") if isinstance(defn, dict) else None
    if not isinstance(params, list):
        return {}
    return {
        str(p["key"]): str(p["field"])
        for p in params
        if isinstance(p, dict) and "key" in p and "field" in p
    }


async def _bump_schedules(session: AsyncSession, schedule_ids: Collection[int]) -> None:
    if not schedule_ids:
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    await session.execute(
        update(TiqoraReportSchedule)
        .where(TiqoraReportSchedule.id.in_(sorted(schedule_ids)))
        .values(version=TiqoraReportSchedule.version + 1, changed_at=now)
    )


async def scrub_schedule_items(
    session: AsyncSession,
    login_to_new: Mapping[str, str],
    company_to_new: Mapping[str, str] | None = None,
    *,
    backup: BackupFn | None = None,
) -> int:
    """Rewrite erased logins / company ids in the runtime parameters of schedule items.

    Only parameters whose report field is ``customer_user`` / ``customer`` are touched (the
    report definition is resolved per item). Items are locked while read; the parent schedule's
    ``version`` / ``changed_at`` are bumped once. Does not commit. Returns the items changed.
    """
    logins = {k: v for k, v in login_to_new.items() if k}
    companies = {k: v for k, v in (company_to_new or {}).items() if k}
    if not logins and not companies:
        return 0
    item = TiqoraReportScheduleItem
    fields_cache: dict[str, dict[str, str]] = {}
    touched: set[int] = set()
    changed = 0
    last: tuple[int, int] | None = None
    while True:
        stmt = (
            select(item.schedule_id, item.position, item.report_key, item.overrides)
            .where(item.overrides.is_not(None))
            .order_by(item.schedule_id, item.position)
            .limit(_BATCH)
            .with_for_update()
        )
        if last is not None:
            stmt = stmt.where(
                or_(
                    item.schedule_id > last[0],
                    and_(item.schedule_id == last[0], item.position > last[1]),
                )
            )
        rows = (await session.execute(stmt)).all()
        if not rows:
            break
        last = (int(rows[-1][0]), int(rows[-1][1]))
        for schedule_id, position, report_key, raw in rows:
            if report_key not in fields_cache:
                fields_cache[report_key] = await _parameter_fields(session, report_key)
            new_raw = _scrub_overrides(raw, fields_cache[report_key], logins, companies)
            if new_raw is None:
                continue
            if backup is not None:
                await backup(
                    SCHEDULE_ITEM_TABLE,
                    {"schedule_id": int(schedule_id), "position": int(position)},
                    {"overrides": raw},
                )
            await session.execute(
                update(item)
                .where(item.schedule_id == schedule_id, item.position == position)
                .values(overrides=new_raw)
            )
            touched.add(int(schedule_id))
            changed += 1
    await _bump_schedules(session, touched)
    return changed


async def remove_erased_recipient_emails(session: AsyncSession, emails: Collection[str]) -> int:
    """Delete e-mail recipients equal (case-insensitive) to an erased customer's address.

    Not restored by a rollback (documented in ``docs/gdpr.md``). Bumps the parent schedules'
    ``version`` / ``changed_at``. Does not commit. Returns the number of recipient rows removed.
    """
    lowered = sorted({e.strip().casefold() for e in emails if e and e.strip()})
    if not lowered:
        return 0
    rec = TiqoraReportScheduleRecipient
    rows = (
        await session.execute(
            select(rec.id, rec.schedule_id)
            .where(rec.kind == "email", func.lower(rec.email).in_(lowered))
            .with_for_update()
        )
    ).all()
    if not rows:
        return 0
    await session.execute(delete(rec).where(rec.id.in_([int(r[0]) for r in rows])))
    await _bump_schedules(session, {int(r[1]) for r in rows})
    return len(rows)
