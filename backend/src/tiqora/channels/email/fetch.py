"""Mail fetch: read ``mail_account`` rows and fetch messages via IMAP/IMAPS/POP3/POP3S.

Behavioural port of ``Kernel/System/MailAccount/{IMAP,IMAPS,POP3,POP3S}.pm``:

- ``mail_account.pw`` is plaintext (Znuny does not obfuscate it) — read verbatim.
- OAuth2 accounts (``authentication_type = oauth2_token``) authenticate via
  SASL XOAUTH2 using the shared legacy ``oauth2_token`` table (Znuny 6.3+).
- Messages larger than ``PostMasterMaxEmailSize`` (KB) are logged and skipped
  (not handed to the pipeline), matching Znuny's oversized-message handling.
- Znuny **deletes** processed messages from the mailbox after fetch. Tiqora
  replicates this by default; set the ``daemon.postmaster.leave_on_server``
  tiqora_settings flag to "1" to keep messages on the server (testing only —
  running this against a mailbox Znuny's own daemon also polls will duplicate
  processing).

The blocking ``imaplib``/``poplib`` stdlib clients are wrapped with
``asyncio.to_thread`` rather than pulling in an async IMAP dependency — Phase 4a
pragmatic choice, documented in the uncertainties section of
``docs/parallel-operation.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import poplib
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.mail_account import MailAccount

logger = structlog.get_logger(__name__)

ACCOUNT_TYPE_IMAP = "IMAP"
ACCOUNT_TYPE_IMAPS = "IMAPS"
ACCOUNT_TYPE_POP3 = "POP3"
ACCOUNT_TYPE_POP3S = "POP3S"

AUTH_PASSWORD = "password"
AUTH_OAUTH2 = "oauth2_token"

IMAP_PORT = 143
IMAPS_PORT = 993
POP3_PORT = 110
POP3S_PORT = 995


@dataclass
class FetchedMessage:
    """One raw RFC822 message pulled from a mailbox."""

    raw: bytes
    uid: str


@dataclass
class FetchResult:
    account_id: int
    messages: list[FetchedMessage]
    oversized: int
    errors: list[str]


@dataclass
class _AuthMaterial:
    """Resolved credentials for one fetch attempt."""

    kind: str  # password | oauth2_token
    password: str | None = None
    access_token: str | None = None


async def list_valid_mail_accounts(session: AsyncSession) -> list[MailAccount]:
    """Return all ``valid_id = 1`` mail accounts (Znuny only polls valid ones).

    On OTRS/Znuny 6.0–6.2 the ``authentication_type`` / ``oauth2_token_config_id``
    columns are absent — :func:`mail_account_load_options` omits them from the
    SELECT when the runtime schema profile says so.
    """
    from tiqora.db.legacy.profile import mail_account_load_options

    rows = (
        (
            await session.execute(
                select(MailAccount)
                .where(MailAccount.valid_id == 1)
                .options(*mail_account_load_options())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def _account_auth_type(account: MailAccount) -> str:
    raw = getattr(account, "authentication_type", None) or AUTH_PASSWORD
    return str(raw).strip().lower() or AUTH_PASSWORD


async def _resolve_auth(session: AsyncSession, account: MailAccount) -> _AuthMaterial | str:
    """Return auth material or an error string."""
    auth_type = _account_auth_type(account)
    if auth_type == AUTH_PASSWORD:
        return _AuthMaterial(kind=AUTH_PASSWORD, password=account.pw or "")
    if auth_type != AUTH_OAUTH2:
        return f"unsupported authentication_type {auth_type!r}"

    config_id = getattr(account, "oauth2_token_config_id", None)
    if not config_id:
        return "oauth2_token_config_id is missing for oauth2_token account"
    try:
        from tiqora.domain.oauth2_mail import OAuth2MailError, get_access_token

        token = await get_access_token(session, config_id=int(config_id), user_id=1)
    except OAuth2MailError as exc:
        return f"OAuth2 token could not be retrieved: {exc}"
    except Exception as exc:  # noqa: BLE001 — surface as fetch error
        logger.exception("oauth2_token_resolve_failed", account_id=account.id)
        return f"OAuth2 token could not be retrieved: {exc}"
    return _AuthMaterial(kind=AUTH_OAUTH2, access_token=token)


def _imap_connect(account: MailAccount, auth: _AuthMaterial) -> imaplib.IMAP4:
    if account.account_type.upper() == ACCOUNT_TYPE_IMAPS:
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(account.host, IMAPS_PORT, timeout=60)
    else:
        conn = imaplib.IMAP4(account.host, IMAP_PORT, timeout=60)
    if auth.kind == AUTH_OAUTH2:
        from tiqora.domain.oauth2_mail import assemble_sasl_xoauth2_raw

        assert auth.access_token is not None
        sasl = assemble_sasl_xoauth2_raw(account.login, auth.access_token)
        sent = {"done": False}

        def _xoauth2_handler(response: bytes) -> bytes | None:
            # imaplib base64-encodes the return value — return raw SASL once.
            _ = response
            if sent["done"]:
                return None
            sent["done"] = True
            return sasl

        conn.authenticate("XOAUTH2", _xoauth2_handler)
    else:
        conn.login(account.login, auth.password or "")
    return conn


def _pop3_xoauth2(conn: poplib.POP3, login: str, access_token: str, *, split_method: bool) -> None:
    """Authenticate POP3 with XOAUTH2 (Gmail one-shot; Office365 split)."""
    from tiqora.domain.oauth2_mail import assemble_sasl_xoauth2_b64

    b64 = assemble_sasl_xoauth2_b64(login, access_token)
    if split_method:
        # Office 365: AUTH XOAUTH2 → then token line (Znuny SplitOAuth2MethodAndToken).
        resp = conn._shortcmd("AUTH XOAUTH2")  # noqa: SLF001 — poplib has no public AUTH
        if not resp.startswith(b"+"):
            raise poplib.error_proto(resp)
        resp2 = conn._shortcmd(b64)  # noqa: SLF001
        if not resp2.startswith(b"+OK"):
            raise poplib.error_proto(resp2)
    else:
        resp = conn._shortcmd(f"AUTH XOAUTH2 {b64}")  # noqa: SLF001
        if not resp.startswith(b"+OK"):
            raise poplib.error_proto(resp)


def _office365_host(host: str) -> bool:
    h = host.lower()
    return "office365" in h or "outlook.office" in h or h.endswith("office.com")


def _fetch_imap_sync(
    account: MailAccount,
    *,
    max_size_bytes: int,
    leave_on_server: bool,
    auth: _AuthMaterial,
) -> FetchResult:
    messages: list[FetchedMessage] = []
    errors: list[str] = []
    oversized = 0
    conn: imaplib.IMAP4 | None = None
    try:
        conn = _imap_connect(account, auth)
        folder = account.imap_folder or "INBOX"
        status, _ = conn.select(folder)
        if status != "OK":
            errors.append(f"cannot select folder {folder!r}")
            return FetchResult(account_id=account.id, messages=[], oversized=0, errors=errors)

        status, data = conn.uid("search", "ALL")
        if status != "OK" or not data or not data[0]:
            return FetchResult(account_id=account.id, messages=[], oversized=0, errors=[])

        uids = data[0].split()
        for uid in uids:
            uid_str = uid.decode("ascii")
            try:
                size_status, size_data = conn.uid("fetch", uid_str, "(RFC822.SIZE)")
                size = None
                if size_status == "OK" and size_data and size_data[0]:
                    head = size_data[0]
                    if isinstance(head, bytes) and b"RFC822.SIZE" in head:
                        try:
                            size = int(head.split(b"RFC822.SIZE")[1].split(b")")[0].strip())
                        except (ValueError, IndexError):
                            size = None
                if size is not None and size > max_size_bytes:
                    oversized += 1
                    logger.warning(
                        "postmaster_message_oversized",
                        account_id=account.id,
                        uid=uid_str,
                        size=size,
                        max_size=max_size_bytes,
                    )
                    if not leave_on_server:
                        conn.uid("store", uid_str, "+FLAGS", r"(\Deleted)")
                    continue

                fetch_status, fetch_data = conn.uid("fetch", uid_str, "(RFC822)")
                if fetch_status != "OK" or not fetch_data:
                    errors.append(f"fetch failed for uid {uid_str}")
                    continue
                raw = b""
                for part in fetch_data:
                    if isinstance(part, tuple) and len(part) >= 2:
                        raw = part[1]
                        break
                if not raw:
                    errors.append(f"empty body for uid {uid_str}")
                    continue
                messages.append(FetchedMessage(raw=raw, uid=uid_str))
                if not leave_on_server:
                    conn.uid("store", uid_str, "+FLAGS", r"(\Deleted)")
            except OSError as exc:  # noqa: PERF203 — per-message isolation
                errors.append(f"uid {uid_str}: {exc}")

        if not leave_on_server:
            conn.expunge()
    except (OSError, imaplib.IMAP4.error) as exc:
        errors.append(str(exc))
    finally:
        if conn is not None:
            with contextlib.suppress(OSError, imaplib.IMAP4.error):
                conn.logout()

    return FetchResult(account_id=account.id, messages=messages, oversized=oversized, errors=errors)


def _fetch_pop3_sync(
    account: MailAccount,
    *,
    max_size_bytes: int,
    leave_on_server: bool,
    auth: _AuthMaterial,
) -> FetchResult:
    messages: list[FetchedMessage] = []
    errors: list[str] = []
    oversized = 0
    conn: poplib.POP3 | None = None
    try:
        if account.account_type.upper() == ACCOUNT_TYPE_POP3S:
            conn = poplib.POP3_SSL(account.host, POP3S_PORT, timeout=60)
        else:
            conn = poplib.POP3(account.host, POP3_PORT, timeout=60)
        if auth.kind == AUTH_OAUTH2:
            assert auth.access_token is not None
            _pop3_xoauth2(
                conn,
                account.login,
                auth.access_token,
                split_method=_office365_host(account.host),
            )
        else:
            conn.user(account.login)
            conn.pass_(auth.password or "")

        count, _size = conn.stat()
        for msg_num in range(1, count + 1):
            try:
                _resp, msg_size_lines, _octets = conn.top(msg_num, 0)
                approx_size = sum(len(line) for line in msg_size_lines)
                if approx_size > max_size_bytes:
                    oversized += 1
                    logger.warning(
                        "postmaster_message_oversized",
                        account_id=account.id,
                        uid=str(msg_num),
                        size=approx_size,
                        max_size=max_size_bytes,
                    )
                    if not leave_on_server:
                        conn.dele(msg_num)
                    continue

                _resp, lines, _octets = conn.retr(msg_num)
                raw = b"\r\n".join(lines)
                messages.append(FetchedMessage(raw=raw, uid=str(msg_num)))
                if not leave_on_server:
                    conn.dele(msg_num)
            except poplib.error_proto as exc:  # noqa: PERF203
                errors.append(f"msg {msg_num}: {exc}")

        conn.quit()
        conn = None
    except (OSError, poplib.error_proto) as exc:
        errors.append(str(exc))
    finally:
        if conn is not None:
            with contextlib.suppress(OSError, poplib.error_proto):
                conn.quit()

    return FetchResult(account_id=account.id, messages=messages, oversized=oversized, errors=errors)


async def fetch_account(
    account: MailAccount,
    *,
    max_size_kb: int,
    leave_on_server: bool,
    session: AsyncSession | None = None,
) -> FetchResult:
    """Fetch and (unless *leave_on_server*) delete all messages for one account.

    *session* is required when the account uses ``authentication_type=oauth2_token``
    so the access token can be loaded/refreshed from ``oauth2_token``. Password
    accounts ignore *session*.
    """
    max_size_bytes = max_size_kb * 1024
    account_type = account.account_type.upper()

    auth_type = _account_auth_type(account)
    if auth_type == AUTH_OAUTH2:
        if session is None:
            return FetchResult(
                account_id=account.id,
                messages=[],
                oversized=0,
                errors=["oauth2_token auth requires a database session"],
            )
        resolved = await _resolve_auth(session, account)
        if isinstance(resolved, str):
            return FetchResult(account_id=account.id, messages=[], oversized=0, errors=[resolved])
        auth = resolved
    else:
        auth = _AuthMaterial(kind=AUTH_PASSWORD, password=account.pw or "")

    if account_type in (ACCOUNT_TYPE_IMAP, ACCOUNT_TYPE_IMAPS):
        return await asyncio.to_thread(
            _fetch_imap_sync,
            account,
            max_size_bytes=max_size_bytes,
            leave_on_server=leave_on_server,
            auth=auth,
        )
    if account_type in (ACCOUNT_TYPE_POP3, ACCOUNT_TYPE_POP3S):
        return await asyncio.to_thread(
            _fetch_pop3_sync,
            account,
            max_size_bytes=max_size_bytes,
            leave_on_server=leave_on_server,
            auth=auth,
        )
    return FetchResult(
        account_id=account.id,
        messages=[],
        oversized=0,
        errors=[f"unsupported account_type {account.account_type!r}"],
    )
