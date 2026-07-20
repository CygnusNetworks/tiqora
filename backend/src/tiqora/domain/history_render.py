"""Human-readable rendering of ``ticket_history`` rows.

Znuny stores history entries as ``%%``-delimited positional values in
``ticket_history.name`` and expands them at display time via the
``Ticket::Frontend::HistoryTypes`` sprintf-style template map (see
``znuny-6.5.22/Kernel/Config/Files/XML/Ticket.xml``, and the assembly logic in
``znuny-6.5.22/Kernel/Modules/AgentTicketHistory.pm``).

This module ports that mapping against the *exact* ``%%`` formats Tiqora's own
write path produces (``tiqora.znuny.history`` — see the table in its module
docstring), plus a couple of formats Tiqora defines itself for actions Znuny's
default config does not template in detail (``Forward``, ``Bounce`` — Tiqora
mirrors Znuny's own ``Forwarded to "%s".`` / ``Bounced to "%s".`` wording).

Rendering never raises: malformed/legacy/unknown ``name`` strings fall back to
a best-effort readable form instead of surfacing raw ``%%`` control characters
to agents.
"""

from __future__ import annotations

from collections.abc import Callable

UserResolver = Callable[[int | str | None], str | None]


def _split(name: str) -> list[str]:
    """Split a Znuny ``%%``-prefixed history name into its positional values.

    ``"%%a%%b%%c"`` -> ``["a", "b", "c"]``. A leading ``%%`` is required by
    convention but tolerated if missing; trailing empty segments (from a
    trailing ``%%``) are kept so callers can tell a field was explicitly
    empty vs. absent.
    """
    stripped = name[2:] if name.startswith("%%") else name
    return stripped.split("%%")


def _get(values: list[str], idx: int, default: str = "") -> str:
    if 0 <= idx < len(values):
        v = values[idx].strip()
        return v if v else default
    return default


def _is_numeric(value: str) -> bool:
    return value.strip().isdigit()


def render_history_entry(
    *,
    history_type: str | None,
    name: str,
    resolve_user: UserResolver | None = None,
) -> str:
    """Render one history row's ``name`` as a readable, localized-ready sentence.

    ``resolve_user`` optionally maps a numeric user id (as it appears in the
    ``%%`` payload) to a login/display name; when it returns ``None`` the raw
    id is kept as a fallback. Strings are plain (not yet i18n-translated) —
    the API returns English sentences matching Tiqora's other server-rendered
    strings (history detail is not currently part of the i18n catalogue);
    the frontend renders ``rendered`` verbatim.
    """
    raw = (name or "").strip()
    htype = history_type or ""
    is_empty = not raw or raw in {"%%", "%"}
    is_encoded = raw.startswith("%%")
    values = _split(raw) if is_encoded else ([] if is_empty else [raw])

    renderer = _RENDERERS.get(htype)
    if renderer is not None:
        try:
            return renderer(values, resolve_user)
        except Exception:  # noqa: BLE001 - never let a bad row break the list
            pass

    if is_empty:
        return _fallback_by_type(history_type)

    if not is_encoded:
        return raw

    # Unknown/legacy type with %%-encoded payload: join non-empty, non-purely
    # -numeric-internal-id trailing values into a readable, generic sentence
    # rather than showing raw control characters.
    parts = [v.strip() for v in values if v.strip()]
    if not parts:
        return _fallback_by_type(history_type)
    # Drop a single trailing purely-numeric value — in every Tiqora/Znuny
    # format that's an internal id (ticket id, article id, ...), never
    # meaningful to an agent on its own.
    if len(parts) > 1 and _is_numeric(parts[-1]):
        parts = parts[:-1]
    label = htype or "History"
    return f"{label}: {', '.join(parts)}"


def _fallback_by_type(history_type: str | None) -> str:
    if history_type == "FollowUp":
        return "Follow-up added."
    if history_type:
        return f"{history_type}."
    return "(no detail)"


def _resolve(resolve_user: UserResolver | None, raw_id: str) -> str:
    if resolve_user is None or not raw_id:
        return raw_id
    if not _is_numeric(raw_id):
        return raw_id
    resolved = resolve_user(int(raw_id))
    return resolved if resolved else raw_id


# ---------------------------------------------------------------------------
# Per-type renderers — one per tiqora.znuny.history ``TYPE_*`` format.
# ---------------------------------------------------------------------------


def _r_new_ticket(v: list[str], _: UserResolver | None) -> str:
    # %%TN%%Queue%%Priority%%State%%TicketID (TicketID dropped: internal)
    tn = _get(v, 0)
    queue = _get(v, 1)
    priority = _get(v, 2)
    state = _get(v, 3)
    return f'Ticket {tn} created in queue "{queue}" with priority "{priority}" and state "{state}".'


def _r_state_update(v: list[str], _: UserResolver | None) -> str:
    old, new = _get(v, 0), _get(v, 1)
    return f'State changed from "{old}" to "{new}".'


def _r_move(v: list[str], _: UserResolver | None) -> str:
    new_q, _new_qid, old_q, _old_qid = _get(v, 0), _get(v, 1), _get(v, 2), _get(v, 3)
    if old_q:
        return f'Queue changed to "{new_q}" from "{old_q}".'
    return f'Queue changed to "{new_q}".'


def _r_title_update(v: list[str], _: UserResolver | None) -> str:
    old, new = _get(v, 0), _get(v, 1)
    return f'Title changed from "{old}" to "{new}".'


def _r_type_update(v: list[str], _: UserResolver | None) -> str:
    new_t, _new_id, old_t, _old_id = _get(v, 0), _get(v, 1), _get(v, 2), _get(v, 3)
    return f'Type changed from "{old_t}" to "{new_t}".'


def _r_service_update(v: list[str], _: UserResolver | None) -> str:
    new_s, _nid, old_s, _oid = _get(v, 0), _get(v, 1), _get(v, 2), _get(v, 3)
    return (
        f'Service changed to "{new_s}".'
        if not old_s
        else f'Service changed from "{old_s}" to "{new_s}".'
    )


def _r_sla_update(v: list[str], _: UserResolver | None) -> str:
    new_s, _nid, old_s, _oid = _get(v, 0), _get(v, 1), _get(v, 2), _get(v, 3)
    return (
        f'SLA changed to "{new_s}".' if not old_s else f'SLA changed from "{old_s}" to "{new_s}".'
    )


def _r_priority_update(v: list[str], _: UserResolver | None) -> str:
    old_p, _oid, new_p, _nid = _get(v, 0), _get(v, 1), _get(v, 2), _get(v, 3)
    return f'Priority changed from "{old_p}" to "{new_p}".'


def _r_owner_update(v: list[str], resolve_user: UserResolver | None) -> str:
    login = _get(v, 0)
    return f"Owner set to {login}."


def _r_responsible_update(v: list[str], resolve_user: UserResolver | None) -> str:
    login = _get(v, 0)
    return f"Responsible set to {login}."


def _r_lock(v: list[str], _: UserResolver | None) -> str:
    return "Ticket locked."


def _r_unlock(v: list[str], _: UserResolver | None) -> str:
    return "Ticket unlocked."


def _r_customer_update(v: list[str], _: UserResolver | None) -> str:
    # %%CustomerID=X;CustomerUser=Y; (either part may be absent)
    raw = v[0] if v else ""
    parts = [p for p in raw.split(";") if p.strip()]
    readable = []
    for p in parts:
        if "=" not in p:
            continue
        key, _sep, val = p.partition("=")
        label = (
            "Customer ID"
            if key == "CustomerID"
            else "Customer user"
            if key == "CustomerUser"
            else key
        )
        readable.append(f'{label} set to "{val}"')
    return (", ".join(readable) + ".") if readable else "Customer updated."


def _r_pending_time(v: list[str], _: UserResolver | None) -> str:
    return f"Pending time set to {_get(v, 0)}." if v and v[0] else "Pending time set."


def _r_subscribe(v: list[str], _: UserResolver | None) -> str:
    return f"{_get(v, 0, 'A user')} started watching this ticket."


def _r_unsubscribe(v: list[str], _: UserResolver | None) -> str:
    return f"{_get(v, 0, 'A user')} stopped watching this ticket."


def _r_dynamic_field_update(v: list[str], _: UserResolver | None) -> str:
    # %%FieldName%%<name>%%Value%%<new>%%OldValue%%<old>
    field = _get(v, 1)
    new_v = _get(v, 3)
    old_v = _get(v, 5)
    if old_v:
        return f'Field "{field}" changed from "{old_v}" to "{new_v}".'
    return f'Field "{field}" set to "{new_v}".'


def _r_archive_flag_update(v: list[str], _: UserResolver | None) -> str:
    flag = _get(v, 0)
    return "Ticket archived." if flag == "y" else "Ticket unarchived."


def _r_merged(v: list[str], _: UserResolver | None) -> str:
    # %%MergeTN%%MergeTicketID%%MainTN%%MainTicketID
    merge_tn, _mid, main_tn, _maid = _get(v, 0), _get(v, 1), _get(v, 2), _get(v, 3)
    return f"Merged into ticket {main_tn}." if main_tn else f"Merged ticket {merge_tn}."


def _r_misc(v: list[str], _: UserResolver | None) -> str:
    return _get(v, 0) or "—"


def _r_forward(v: list[str], _: UserResolver | None) -> str:
    return f'Forwarded to "{_get(v, 0)}".' if v and v[0] else "Forwarded."


def _r_bounce(v: list[str], _: UserResolver | None) -> str:
    return f'Bounced to "{_get(v, 0)}".' if v and v[0] else "Bounced."


def _article_free_text(label: str) -> Callable[[list[str], UserResolver | None], str]:
    def _render(v: list[str], _: UserResolver | None) -> str:
        text = " ".join(p for p in v if p.strip())
        return f"{label}: {text}" if text else f"{label}."

    return _render


_RENDERERS: dict[str, Callable[[list[str], UserResolver | None], str]] = {
    "NewTicket": _r_new_ticket,
    "StateUpdate": _r_state_update,
    "Move": _r_move,
    "TitleUpdate": _r_title_update,
    "TypeUpdate": _r_type_update,
    "ServiceUpdate": _r_service_update,
    "SLAUpdate": _r_sla_update,
    "PriorityUpdate": _r_priority_update,
    "OwnerUpdate": _r_owner_update,
    "ResponsibleUpdate": _r_responsible_update,
    "Lock": _r_lock,
    "Unlock": _r_unlock,
    "CustomerUpdate": _r_customer_update,
    "SetPendingTime": _r_pending_time,
    "Subscribe": _r_subscribe,
    "Unsubscribe": _r_unsubscribe,
    "TicketDynamicFieldUpdate": _r_dynamic_field_update,
    "ArchiveFlagUpdate": _r_archive_flag_update,
    "Merged": _r_merged,
    "Misc": _r_misc,
    "Forward": _r_forward,
    "Bounce": _r_bounce,
    "AddNote": _article_free_text("Note added"),
    "EmailAgent": lambda v, r: "Email sent to customer.",
    "EmailCustomer": _article_free_text("Email received"),
    "SendAnswer": _article_free_text("Email sent"),
    "PhoneCallAgent": lambda v, r: "Phone call to customer logged.",
    "PhoneCallCustomer": lambda v, r: "Phone call from customer logged.",
    "FollowUp": _article_free_text("Follow-up"),
    "WebRequestCustomer": lambda v, r: "Web request received from customer.",
}
