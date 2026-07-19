"""Mail fetch: read ``mail_account`` rows and fetch messages via IMAP/IMAPS/POP3/POP3S.

Behavioural port of ``Kernel/System/MailAccount/{IMAP,IMAPS,POP3,POP3S}.pm``:

- ``mail_account.pw`` is plaintext (Znuny does not obfuscate it) — read verbatim.
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


async def list_valid_mail_accounts(session: AsyncSession) -> list[MailAccount]:
    """Return all ``valid_id = 1`` mail accounts (Znuny only polls valid ones)."""
    rows = (
        (await session.execute(select(MailAccount).where(MailAccount.valid_id == 1)))
        .scalars()
        .all()
    )
    return list(rows)


def _imap_connect(account: MailAccount) -> imaplib.IMAP4:
    if account.account_type.upper() == ACCOUNT_TYPE_IMAPS:
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(account.host, IMAPS_PORT, timeout=60)
    else:
        conn = imaplib.IMAP4(account.host, IMAP_PORT, timeout=60)
    conn.login(account.login, account.pw)
    return conn


def _fetch_imap_sync(
    account: MailAccount, *, max_size_bytes: int, leave_on_server: bool
) -> FetchResult:
    messages: list[FetchedMessage] = []
    errors: list[str] = []
    oversized = 0
    conn: imaplib.IMAP4 | None = None
    try:
        conn = _imap_connect(account)
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
    account: MailAccount, *, max_size_bytes: int, leave_on_server: bool
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
        conn.user(account.login)
        conn.pass_(account.pw)

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
    account: MailAccount, *, max_size_kb: int, leave_on_server: bool
) -> FetchResult:
    """Fetch and (unless *leave_on_server*) delete all messages for one account."""
    max_size_bytes = max_size_kb * 1024
    account_type = account.account_type.upper()
    if account_type in (ACCOUNT_TYPE_IMAP, ACCOUNT_TYPE_IMAPS):
        return await asyncio.to_thread(
            _fetch_imap_sync,
            account,
            max_size_bytes=max_size_bytes,
            leave_on_server=leave_on_server,
        )
    if account_type in (ACCOUNT_TYPE_POP3, ACCOUNT_TYPE_POP3S):
        return await asyncio.to_thread(
            _fetch_pop3_sync,
            account,
            max_size_bytes=max_size_bytes,
            leave_on_server=leave_on_server,
        )
    return FetchResult(
        account_id=account.id,
        messages=[],
        oversized=0,
        errors=[f"unsupported account_type {account.account_type!r}"],
    )
