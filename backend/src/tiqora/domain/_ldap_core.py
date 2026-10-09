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
   ``(<access_attr>=<user DN or login>)`` under ``base=<group DN>``. No hit = not a
   member; result noSuchObject (the group does not exist) = not a member, with a
   warning naming the stale rule's group DN (Znuny ``next GROUPDN``). Any other search
   ERROR (transport error, timeout, referral, other result codes, malformed DN) sets
   ``group_search_failed`` so role/group sync is skipped for that login — a deliberate
   deviation from Znuny, which treats every failure as "not a member" and strips roles.
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
* TLS: ``tls_verify`` checks the certificate chain (system CAs or ``ca_certs_pem``) and
  the host name; env directories default to unverified for compatibility (startup warning).
* Network: connect timeout 5 s, receive timeout 10 s, referrals are never chased
  (Net::LDAP does not follow them either: a referral is a failed search). The worker
  thread also gets an absolute per-login deadline: no operation starts after it, and
  every socket timeout is capped at the time left.
* Several user entries for one login are rejected as ambiguous (Znuny takes the last).
* Matching the resolved UID to a local account (and creating one) is the caller's
  job (``tiqora.identity.ldap_login`` + ``tiqora.identity.login``), not this module's.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Attributes whose values are binary identifiers: always hex-encoded, never decoded.
# objectGUID becomes the hex of its raw (little-endian-field) bytes, not the canonical
# "{xxxxxxxx-...}" string AD tools display.
_BINARY_ID_ATTRS = frozenset({"objectguid"})
# Network bounds: an unreachable or stalled directory must not hold a login (or an
# executor thread) for long. The login chain adds an overall budget on top.
CONNECT_TIMEOUT_SECONDS = 5
RECEIVE_TIMEOUT_SECONDS = 10
_LDAP_SUCCESS = 0


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
    user_attr: str = "DN"  # exactly "DN" compares the user DN, anything else the login
    tls_verify: bool = False  # LdapProviderConfig defaults to True; env keeps the old default
    ca_certs_pem: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class LdapEntry:
    """A successfully authenticated directory user."""

    uid: str
    dn: str
    attributes: dict[str, tuple[str, ...]]
    member_group_dns: tuple[str, ...]  # subset of the requested group DNs the user is in
    # True when at least one rule-group search ERRORED (not "no match"): membership is
    # unknown, so role/group sync must be skipped for this login (fail safe).
    group_search_failed: bool = False


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


_NO_SUCH_OBJECT = 32
# Default time for one directory login when the caller passes no deadline.
DEFAULT_LOGIN_BUDGET_SECONDS = 20.0


class LdapDirectoryError(Exception):
    """Connecting, binding or searching the directory failed (public helpers)."""


class _SearchError(Exception):  # noqa: N818 - internal control flow
    """A search raised or returned a non-success result (referral, noSuchObject, …)."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class _DeadlineExceeded(Exception):  # noqa: N818 - internal control flow
    """The per-login time budget is used up; no further LDAP operation is started."""


@dataclass(frozen=True, slots=True)
class _Deadline:
    """Absolute ``time.monotonic()`` deadline of one directory login.

    ``receive_timeout`` applies per ``recv()``, so a directory that keeps trickling
    bytes could hold the executor thread far past the caller's budget. Before every
    operation the remaining time is checked (abort when used up) and the socket timeout
    is capped at ``min(RECEIVE_TIMEOUT_SECONDS, remaining)``.
    """

    at: float

    def remaining(self) -> float:
        return self.at - time.monotonic()

    def arm(self, conn: Any, op: str) -> None:
        left = self.remaining()
        if left <= 0:
            raise _DeadlineExceeded(op)
        timeout = min(float(RECEIVE_TIMEOUT_SECONDS), left)
        conn.receive_timeout = timeout
        sock = getattr(conn, "socket", None)
        if sock is not None:
            try:
                sock.settimeout(timeout)
            except Exception:  # noqa: BLE001 - closed/mock socket: the next op reports it
                logger.debug("ldap_socket_timeout_not_set", op=op)


def _deadline(at: float | None) -> _Deadline:
    return _Deadline(at if at is not None else time.monotonic() + DEFAULT_LOGIN_BUDGET_SECONDS)


def _search(
    conn: Any, base: str, filt: str, attributes: Sequence[str], deadline: _Deadline
) -> list[dict[str, Any]]:
    """Entries of one search; ``_SearchError`` on an exception or a non-success result.

    Referrals are not chased (``auto_referrals=False``, Znuny/Net::LDAP parity): a
    referral answer is a failed search, never a bind to another host.
    """
    deadline.arm(conn, "search")
    try:
        conn.search(search_base=base, search_filter=filt, attributes=list(attributes))
    except Exception as exc:  # noqa: BLE001 - e.g. LDAPInvalidDnError, socket timeout
        raise _SearchError(type(exc).__name__) from exc
    result = conn.result if isinstance(conn.result, dict) else {}
    code = result.get("result", _LDAP_SUCCESS)
    if code != _LDAP_SUCCESS:
        raise _SearchError(str(result.get("description") or code), code=code)
    return _entries(conn)


def _tls(ldap3: Any, config: LdapConfig) -> Any:
    """TLS settings: verified chain + host name, or (env compatibility only) unverified."""
    if not config.tls_verify:
        return ldap3.Tls(validate=ssl.CERT_NONE)
    return ldap3.Tls(
        validate=ssl.CERT_REQUIRED,
        ca_certs_data=config.ca_certs_pem or None,
        sni=config.host.strip(),
    )


def _connection(ldap3: Any, config: LdapConfig, deadline: _Deadline) -> Any:
    """Server + search-account connection with TLS settings, timeouts, no referrals."""
    left = deadline.remaining()
    if left <= 0:
        raise _DeadlineExceeded("connect")
    server = ldap3.Server(
        config.host.strip(),
        port=config.port,
        use_ssl=config.use_ssl,
        get_info=ldap3.NONE,
        tls=_tls(ldap3, config),
        connect_timeout=min(float(CONNECT_TIMEOUT_SECONDS), left),
    )
    search_account = bool(config.bind_dn and config.bind_password)
    return ldap3.Connection(
        server,
        user=config.bind_dn if search_account else None,
        password=config.bind_password if search_account else None,
        auto_bind=False,
        auto_referrals=False,
        receive_timeout=min(float(RECEIVE_TIMEOUT_SECONDS), left),
    )


def _bind(conn: Any, config: LdapConfig, deadline: _Deadline, *, what: str) -> bool:
    """Open, negotiate StartTLS when configured (before any credentials), then bind."""
    deadline.arm(conn, "open")
    conn.open()
    if config.use_starttls:
        deadline.arm(conn, "start_tls")
        if not conn.start_tls():
            logger.error(
                "ldap_starttls_failed", host=config.host, step=what, error=str(conn.result)
            )
            return False
    deadline.arm(conn, "bind")
    if not conn.bind():
        logger.error("ldap_first_bind_failed", host=config.host, error=str(conn.result))
        return False
    return True


def _unbind(conn: Any, host: str) -> None:
    try:
        conn.unbind()
    except Exception:  # noqa: BLE001 — closing a broken connection
        logger.debug("ldap_unbind_failed", host=host)


def open_search_connection(config: LdapConfig, *, deadline: float | None = None) -> Any:
    """A bound search-account connection with the shared TLS/timeout/referral settings.

    Synchronous (call it in an executor). Raises :class:`LdapDirectoryError`. Used by
    other directory readers (S/MIME ``FetchFromCustomer``); close it with ``unbind()``.
    """
    ldap3 = _import_ldap3()
    limit = _deadline(deadline)
    try:
        conn = _connection(ldap3, config, limit)
    except Exception as exc:
        raise LdapDirectoryError(f"connect failed: {type(exc).__name__}") from exc
    try:
        ok = _bind(conn, config, limit, what="search_bind")
    except Exception as exc:
        _unbind(conn, config.host)
        raise LdapDirectoryError(f"bind failed: {type(exc).__name__}") from exc
    if not ok:
        _unbind(conn, config.host)
        raise LdapDirectoryError("bind failed")
    return conn


def search_entries(
    conn: Any,
    base: str,
    search_filter: str,
    attributes: Sequence[str],
    *,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Entries of one search on *conn* (deadline-capped); a referral or any other
    non-success result raises :class:`LdapDirectoryError`."""
    try:
        return _search(conn, base, search_filter, attributes, _deadline(deadline))
    except (_SearchError, _DeadlineExceeded) as exc:
        raise LdapDirectoryError(f"search failed: {exc}") from exc


def _authenticate_entry_sync(
    config: LdapConfig,
    login: str,
    password: str,
    attributes: Sequence[str],
    member_of_group_dns: Sequence[str],
    deadline_at: float | None = None,
) -> LdapEntry | None:
    """Bind-search-bind. Returns the entry on success, ``None`` on any failure."""
    login = login.strip()
    if not login or not password:
        return None
    ldap3 = _import_ldap3()
    deadline = _deadline(deadline_at)

    try:
        conn = _connection(ldap3, config, deadline)
    except Exception as exc:  # noqa: BLE001 — any connection error is an auth failure
        logger.error("ldap_connect_failed", host=config.host, error=type(exc).__name__)
        return None

    try:
        if not _bind(conn, config, deadline, what="search_bind"):
            return None
        return _search_and_verify(
            ldap3, conn, config, login, password, attributes, member_of_group_dns, deadline
        )
    except _DeadlineExceeded as exc:
        logger.warning("ldap_login_deadline_exceeded", host=config.host, before=str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 — directory trouble is an auth failure
        # Only the exception type and its text; ldap3 never puts the password in it.
        logger.error("ldap_auth_error", host=config.host, error=f"{type(exc).__name__}: {exc}")
        return None
    finally:
        _unbind(conn, config.host)


def _entries(conn: Any) -> list[dict[str, Any]]:
    return [r for r in (conn.response or []) if r.get("type") == "searchResEntry"]


def _search_and_verify(
    ldap3: Any,
    conn: Any,
    config: LdapConfig,
    login: str,
    password: str,
    attributes: Sequence[str],
    member_of_group_dns: Sequence[str],
    deadline: _Deadline,
) -> LdapEntry | None:
    filt = f"({config.uid_attr}={_escape_filter_value(ldap3, login)})"
    if config.always_filter:
        filt = f"(&{filt}{config.always_filter})"

    wanted = list(dict.fromkeys(a for a in (config.uid_attr, *attributes) if a))
    try:
        hits = _search(conn, config.base_dn, filt, wanted, deadline)
    except _SearchError as exc:
        logger.warning("ldap_user_search_failed", base_dn=config.base_dn, error=str(exc))
        return None
    if not hits:
        logger.info("ldap_no_user_entry", login=login, base_dn=config.base_dn, filter=filt)
        return None
    if len(hits) > 1:
        # Znuny silently takes the last hit; an ambiguous login must not pick an account.
        logger.warning("ldap_user_ambiguous", login=login, base_dn=config.base_dn, hits=len(hits))
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

    member_value = user_dn if config.user_attr == "DN" else login
    member_filter = f"({config.access_attr}={_escape_filter_value(ldap3, member_value)})"

    if config.access_attr and config.group_dn:
        try:
            gate = _search(conn, config.group_dn, member_filter, ["1.1"], deadline)
        except _SearchError as exc:  # any error, noSuchObject included: deny
            logger.warning("ldap_group_search_failed", group_dn=config.group_dn, error=str(exc))
            return None
        if not gate:
            logger.info(
                "ldap_group_membership_missing",
                login=login,
                group_dn=config.group_dn,
                filter=member_filter,
            )
            return None

    members: list[str] = []
    group_search_failed = False
    for group_dn in dict.fromkeys(g for g in member_of_group_dns if g and g.strip()):
        try:
            if _search(conn, group_dn, member_filter, ["1.1"], deadline):
                members.append(group_dn)
        except _SearchError as exc:
            if exc.code == _NO_SUCH_OBJECT:
                # The group does not exist: definitively not a member (Znuny parity). The
                # rule is stale; tell the admin which one.
                logger.warning(
                    "ldap_rule_group_missing",
                    group_dn=group_dn,
                    hint="an ldap:group_dn rule names a group that does not exist; fix the rule",
                )
                continue
            # Transport error, referral, other result codes: membership unknown -> the
            # login's role/group sync is skipped (deviation from Znuny, which strips).
            group_search_failed = True
            logger.warning("ldap_rule_group_search_failed", group_dn=group_dn, error=str(exc))

    if config.use_starttls and not getattr(conn, "tls_started", False):
        logger.error("ldap_starttls_not_active", host=config.host)
        return None

    # Re-bind as the resolved user DN with the supplied password on the same (already
    # TLS-protected) connection: this is the actual credential check.
    deadline.arm(conn, "rebind")
    if not conn.rebind(user=user_dn, password=password):
        logger.info("ldap_user_bind_failed", login=login, user_dn=user_dn)
        return None

    logger.info("ldap_auth_ok", login=login, user_dn=user_dn)
    return LdapEntry(
        uid=user_uid,
        dn=user_dn,
        attributes=entry_attrs,
        member_group_dns=tuple(members),
        group_search_failed=group_search_failed,
    )


async def ldap_authenticate_entry(
    config: LdapConfig,
    login: str,
    password: str,
    *,
    attributes: Sequence[str] = (),
    member_of_group_dns: Sequence[str] = (),
    deadline: float | None = None,
) -> LdapEntry | None:
    """Bind-search-bind via ``run_in_executor``; the entry with attributes and rule-group
    membership on success, ``None`` on any failure (empty login/password included).

    *deadline* is an absolute ``time.monotonic()`` value bounding the worker thread
    itself (default: now + :data:`DEFAULT_LOGIN_BUDGET_SECONDS`)."""
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
        deadline,
    )


async def ldap_authenticate(config: LdapConfig, login: str, password: str) -> str | None:
    """Bind-search-bind; returns the LDAP UID on success (see :func:`ldap_authenticate_entry`)."""
    entry = await ldap_authenticate_entry(config, login, password)
    return entry.uid if entry is not None else None
