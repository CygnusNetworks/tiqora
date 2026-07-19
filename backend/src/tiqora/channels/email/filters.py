"""Postmaster DB filters — port of ``Kernel::System::PostMaster::Filter::MatchDBSource``.

Reads ``postmaster_filter`` rows (grouped by ``f_name``), matches ``f_type =
'Match'`` rows against the email's ``GetParam``-style header dict (regex,
case-insensitive, with ``f_not`` negation), and applies ``f_type = 'Set'``
rows (which may set pseudo ``X-OTRS-*`` headers) when **all** Match rows for
that filter name succeed. ``f_stop`` (StopAfterMatch) halts further filter
processing for this message once one filter matches.

Each stored row already carries its own ``f_not`` flag (the composite primary
key is ``f_name, f_type, f_key, f_value``) — unlike the Perl ``FilterAdd`` call
shape (parallel ``Match``/``Not`` arrays matched positionally), no separate
join/positional correlation is required here.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.config import PostmasterFilter

_CAPTURE_SUB_RE = re.compile(r"\[\*\*\s*(\w+)\s*\*\*\]")


async def apply_filters(session: AsyncSession, get_param: dict[str, str]) -> dict[str, str]:
    """Mutate and return *get_param* in place after applying all matching filters."""
    rows = (
        (await session.execute(select(PostmasterFilter).order_by(PostmasterFilter.f_name)))
        .scalars()
        .all()
    )

    by_name: dict[str, list[PostmasterFilter]] = {}
    for row in rows:
        by_name.setdefault(row.f_name, []).append(row)

    for name in sorted(by_name):
        filter_rows = by_name[name]
        match_rows = [r for r in filter_rows if r.f_type == "Match"]
        set_rows = [r for r in filter_rows if r.f_type == "Set"]
        stop_after_match = any(bool(r.f_stop) for r in filter_rows)

        if not match_rows:
            continue

        matched_result = ""
        named_captures: dict[str, str] = {}
        all_matched = True
        for match_row in match_rows:
            value = get_param.get(match_row.f_key)
            negate = bool(match_row.f_not)
            if value is None:
                all_matched = False
                continue
            try:
                m = re.search(match_row.f_value, value, re.IGNORECASE)
            except re.error:
                all_matched = False
                continue
            hit = m is not None
            if negate:
                hit = not hit
            if not hit:
                all_matched = False
                continue
            if m is not None:
                if m.groups():
                    matched_result = m.group(1) or matched_result
                named_captures.update({k: v for k, v in (m.groupdict() or {}).items() if v})

        if not all_matched:
            continue

        def _sub_named(mo: re.Match[str], captures: dict[str, str] = named_captures) -> str:
            return captures.get(mo.group(1), "")

        for set_row in set_rows:
            value = set_row.f_value.replace("[***]", matched_result)
            value = _CAPTURE_SUB_RE.sub(_sub_named, value)
            get_param[set_row.f_key] = value

        if stop_after_match:
            break

    return get_param
