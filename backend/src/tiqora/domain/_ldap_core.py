"""Shared bind-search-bind LDAP core, used by both agent and customer auth.

Ports the semantics of Znuny's ``Kernel::System::Auth::LDAP`` /
``Kernel::System::CustomerAuth::LDAP`` and the directory reads of
``Kernel::System::Auth::Sync::LDAP`` (see ``znuny-6.5.22/Kernel/System/Auth/LDAP.pm``,
``.../Auth/Sync/LDAP.pm`` and ``.../CustomerAuth/LDAP.pm``, read-only references):

1. Connect; with ``use_starttls`` the TLS layer is negotiated BEFORE any bind, so
   neither the search account's nor the user's password ever crosses the wire in
   clear text. Bind with the configured search account (``bind_dn`` +
   ``bind_password``), or anonymously when either is unset (Znuny binds with
   ``SearchUserDN`` only when ``SearchUserPw`` is set, too).
2. Search ``base_dn`` for ``(<uid_attr>=<login>)`` (optionally ANDed with
   ``always_filter``) to resolve the user's DN and read the requested attributes.
3. If a group DN is configured, verify the user's DN (or the typed login, depending on
   ``user_attr``) is a member per ``access_attr`` under that group (access gate).
4. Membership of every requested rule group DN with Znuny's AuthSync filter
   ``(<access_attr>=<user DN or login>)`` under ``base=<group DN>``; a failing search
   counts as "not a member" (Znuny ``next GROUPDN``).
5. Re-bind as the resolved DN with the supplied password on the same connection
   (Znuny does the same) — this *is* the actual credential check.

Escaping: every value placed into a search filter goes through ldap3's RFC 4515
``escape_filter_chars``. No DN is ever composed from user input (the user DN comes from
the directory, group DNs from the admin configuration), so RFC 4514 DN escaping has
nothing to apply to. ``always_filter`` is admin configuration and used verbatim, as in
Znuny. An empty login or password is rejected before any network call: a simple bind
with an empty password is an unauthenticated bind that many servers accept (RFC 4513
5.1.2). Passwords are never logged.

``ldap3`` is a synchronous library, so every call is dispatched via
``run_in_executor`` per the project's async rule. Import is indirected through
:func:`_import_ldap3` so tests can substitute a shim (ldap3's own ``MOCK_SYNC``
strategy is used in the real test suite).

Simplifications vs. Znuny:

* No ``Die`` config knob — connection errors are always logged and treated
  as auth failure (never a hard process crash).
* No charset conversion knob (``AuthModule::LDAP::Charset``) — Tiqora is
  UTF-8 throughout.
* No ``UserSuffix`` / ``UserLowerCase`` knobs — callers are expected to pass
  the login as typed; add these back if a deployment needs them.
* No ``NestedGroupSearch`` (AuthSync): rule group membership is direct only.
* TLS certificate validation follows ldap3's defaults (no CA/verify knob yet).
* Matching the resolved UID to a local account (and creating one) is the caller's
  job (``tiqora.identity.ldap_login`` + ``tiqora.identity.login``), not this module's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Attributes whose values are binary identifiers: always hex-encoded, never decoded.
_BINARY_ID_ATTRS = frozenset({"objectguid"})


class LdapUnavailable(Exception):
    """Raised when ldap3 is not installed."""


def _import_ldap3() -> Any:
    try:
        import ldap3
    except ImportError as exc:
        raise LdapUnavailable(
            "ldap3 is not installed; it is a core dependency, this should not happen"
        ) from exc
    return ldap3


@dataclass(frozen=True, slots=True)
class LdapConfig:
    """Directory connection + search parameters for one LDAP backend."""

    host: str
    port: int = 389
    use_ssl: bool = False
    use_starttls: bool = False
    base_dn: str = ""
    bind_dn: str = ""
    bind_password: str = field(default="", repr=False)
    uid_attr: str = "uid"
    always_filter: str = ""
    group_dn: str = ""
    access_attr: str = "memberUid"
    user_attr: str = "DN"


@dataclass(frozen=True, slots=True)
class LdapEntry:
    """A successfully authenticated directory user."""

    uid: str
    dn: str
    attributes: dict[str, tuple[str, ...]]
    member_group_dns: tuple[str, ...]  # subset of the requested group DNs the user is in


def _escape_filter_value(ldap3: Any, value: str) -> str:
    return str(ldap3.utils.conv.escape_filter_chars(value))


def _decode_value(attr: str, raw: Any) -> str | None:
    """One attribute value as text; binary ids as hex, undecodable binaries dropped."""
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, (bytes, bytearray)):
        return str(raw)
    if attr.lower() in _BINARY_ID_ATTRS:
        return bytes(raw).hex()
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _entry_attributes(raw_attributes: Any, wanted: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Requested attributes as text tuples; a requested attribute the directory did not
    return is present with ``()`` (LDAP omits attributes without values)."""
    out: dict[str, tuple[str, ...]] = {}
    # ldap3 hands out a CaseInsensitiveDict (a Mapping, not a dict).
    if isinstance(raw_attributes, Mapping):
        for name, values in raw_attributes.items():
            items = values if isinstance(values, (list, tuple)) else [values]
            decoded = (_decode_value(str(name), v) for v in items)
            out[str(name)] = tuple(v for v in decoded if v is not None)
    present = {k.lower() for k in out}
    for name in wanted:
        if name and name.lower() not in present:
            out[name] = ()
            present.add(name.lower())
    return out


def _bind(conn: Any, config: LdapConfig, *, what: str) -> bool:
    """Open, negotiate StartTLS when configured (before any credentials), then bind."""
    conn.open()
    if config.use_starttls and not conn.start_tls():
        logger.error("ldap_starttls_failed", host=config.host, step=what, error=str(conn.result))
        return False
    if not conn.bind():
        logger.error("ldap_first_bind_failed", host=config.host, error=str(conn.result))
        return False
    return True


def _authenticate_entry_sync(
    config: LdapConfig,
    login: str,
    password: str,
    attributes: Sequence[str],
    member_of_group_dns: Sequence[str],
) -> LdapEntry | None:
    """Bind-search-bind. Returns the entry on success, ``None`` on any failure."""
    login = login.strip()
    if not login or not password:
        return None
    ldap3 = _import_ldap3()

    try:
        server = ldap3.Server(
            config.host,
            port=config.port,
            use_ssl=config.use_ssl,
            get_info=ldap3.NONE,
        )
        search_account = bool(config.bind_dn and config.bind_password)
        conn = ldap3.Connection(
            server,
            user=config.bind_dn if search_account else None,
            password=config.bind_password if search_account else None,
            auto_bind=False,
        )
    except Exception as exc:  # noqa: BLE001 — any connection error is an auth failure
        logger.error("ldap_connect_failed", host=config.host, error=type(exc).__name__)
        return None

    try:
        if not _bind(conn, config, what="search_bind"):
            return None
        return _search_and_verify(
            ldap3, conn, config, login, password, attributes, member_of_group_dns
        )
    except Exception as exc:  # noqa: BLE001 — directory trouble is an auth failure
        # Only the exception type and its text; ldap3 never puts the password in it.
        logger.error("ldap_auth_error", host=config.host, error=f"{type(exc).__name__}: {exc}")
        return None
    finally:
        try:
            conn.unbind()
        except Exception:  # noqa: BLE001 — closing a broken connection
            logger.debug("ldap_unbind_failed", host=config.host)


def _entries(conn: Any) -> list[dict[str, Any]]:
    return [r for r in (conn.response or []) if r.get("type") == "searchResEntry"]


def _has_entry(conn: Any, base: str, filt: str) -> bool:
    """At least one entry under *base* matches *filt*; a failed search counts as none."""
    return bool(conn.search(search_base=base, search_filter=filt, attributes=["1.1"])) and bool(
        _entries(conn)
    )


def _search_and_verify(
    ldap3: Any,
    conn: Any,
    config: LdapConfig,
    login: str,
    password: str,
    attributes: Sequence[str],
    member_of_group_dns: Sequence[str],
) -> LdapEntry | None:
    filt = f"({config.uid_attr}={_escape_filter_value(ldap3, login)})"
    if config.always_filter:
        filt = f"(&{filt}{config.always_filter})"

    wanted = list(dict.fromkeys(a for a in (config.uid_attr, *attributes) if a))
    if not conn.search(search_base=config.base_dn, search_filter=filt, attributes=wanted):
        logger.info("ldap_no_user_entry", login=login, base_dn=config.base_dn, filter=filt)
        return None
    hits = _entries(conn)
    if not hits:
        logger.info("ldap_no_user_entry", login=login, base_dn=config.base_dn, filter=filt)
        return None

    hit = hits[0]
    user_dn = str(hit.get("dn") or "")
    if not user_dn:
        return None
    entry_attrs = _entry_attributes(hit.get("raw_attributes"), wanted)
    uid_values = next(
        (v for k, v in entry_attrs.items() if k.lower() == config.uid_attr.lower()), ()
    )
    user_uid = uid_values[0] if uid_values else login

    member_value = user_dn if config.user_attr.upper() == "DN" else login
    member_filter = f"({config.access_attr}={_escape_filter_value(ldap3, member_value)})"

    if (
        config.access_attr
        and config.group_dn
        and not _has_entry(conn, config.group_dn, member_filter)
    ):
        logger.info(
            "ldap_group_membership_missing",
            login=login,
            group_dn=config.group_dn,
            filter=member_filter,
        )
        return None

    members: list[str] = []
    for group_dn in dict.fromkeys(g for g in member_of_group_dns if g and g.strip()):
        # Znuny AuthSync: no hit OR a failed search (e.g. the group is gone) = not a member.
        if _has_entry(conn, group_dn, member_filter):
            members.append(group_dn)
        else:
            logger.info("ldap_not_in_rule_group", login=login, group_dn=group_dn)

    # Re-bind as the resolved user DN with the supplied password on the same (already
    # TLS-protected) connection: this is the actual credential check.
    if not conn.rebind(user=user_dn, password=password):
        logger.info("ldap_user_bind_failed", login=login, user_dn=user_dn)
        return None

    logger.info("ldap_auth_ok", login=login, user_dn=user_dn)
    return LdapEntry(
        uid=user_uid,
        dn=user_dn,
        attributes=entry_attrs,
        member_group_dns=tuple(members),
    )


async def ldap_authenticate_entry(
    config: LdapConfig,
    login: str,
    password: str,
    *,
    attributes: Sequence[str] = (),
    member_of_group_dns: Sequence[str] = (),
) -> LdapEntry | None:
    """Bind-search-bind via ``run_in_executor``; the entry with attributes and rule-group
    membership on success, ``None`` on any failure (empty login/password included)."""
    if not login.strip() or not password:
        return None
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _authenticate_entry_sync,
        config,
        login,
        password,
        tuple(attributes),
        tuple(member_of_group_dns),
    )


async def ldap_authenticate(config: LdapConfig, login: str, password: str) -> str | None:
    """Bind-search-bind; returns the LDAP UID on success (see :func:`ldap_authenticate_entry`)."""
    entry = await ldap_authenticate_entry(config, login, password)
    return entry.uid if entry is not None else None
