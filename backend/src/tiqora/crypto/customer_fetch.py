"""``SMIME::FetchFromCustomer``: import customer certificates from the customer backend.

Ports ``Kernel::System::Crypt::SMIME::FetchFromCustomer`` with its two callers:

* the postmaster pre-filter ``PostMaster::PreFilterModule###000-SMIMEFetchFromCustomer``
  (:func:`fetch_for_sender`, run before inbound crypto so the sender's
  certificate is in ``SMIME::CertPath`` when the signature is checked), and
* the daily ``Daemon::SchedulerCronTaskManager::Task###RenewCustomerSMIMECertificates``
  (``Maint::SMIME::CustomerCertificate::Renew``, :func:`renew_customer_certificates`):
  for every address of a stored certificate, the customer's current backend
  certificate is added when it is not stored yet — a renewed certificate
  lands next to the old one, nothing is deleted (as in Znuny).

Both only act when ``SMIME`` and ``SMIME::FetchFromCustomer`` are on. The
customer backend attribute ``UserSMIMECertificate`` comes from (Tiqora's
customer data lives in ``customer_user``; Znuny maps the attribute through
the CustomerUser backend map):

* the customer LDAP directory (``TIQORA_CUSTOMER_LDAP_*``): the entry whose
  ``TIQORA_CUSTOMER_LDAP_EMAIL_ATTR`` (``mail``) is the address, attribute
  ``TIQORA_CUSTOMER_LDAP_SMIME_ATTR`` (``userSMIMECertificate``, PKCS#7/DER);
* a ``customer_user`` column named by ``TIQORA_CUSTOMER_SMIME_CERT_COLUMN``
  (PEM, base64 DER/PKCS#7 or binary).

Like Znuny only addresses that belong to a customer user
(``CustomerSearch PostMasterSearch``) are looked up.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.crypto import CryptoError
from tiqora.crypto import keystore as ks
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.customer_keys import convert_cert_format
from tiqora.crypto.smime_store import SmimeStore
from tiqora.db.legacy.customer import CustomerUser

logger = structlog.get_logger(__name__)

PREFILTER_SETTING = "PostMaster::PreFilterModule###000-SMIMEFetchFromCustomer"
_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def fetch_enabled(config: CryptoConfig) -> bool:
    return bool(config.smime.enabled and config.smime.fetch_from_customer)


def _as_bytes(value: Any) -> bytes | None:
    """Normalise a backend value: bytes as-is, PEM text encoded, other text base64-decoded."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        if data.lstrip().startswith(b"-----BEGIN"):
            return data
        try:  # a TEXT column read as bytes by some drivers
            return base64.b64decode(b"".join(data.split()), validate=True)
        except (binascii.Error, ValueError):
            return data or None
    raw = str(value).strip()
    if not raw:
        return None
    if "-----BEGIN" in raw:
        return raw.encode("utf-8")
    try:
        return base64.b64decode("".join(raw.split()), validate=True)
    except (binascii.Error, ValueError):
        return None


# ------------------------------------------------------------------ sources


async def _column_values(session: AsyncSession, column: str, email: str) -> list[bytes]:
    if not _COLUMN_RE.match(column):
        logger.warning("smime_fetch_bad_column", column=column)
        return []
    try:
        rows = (
            await session.execute(
                text(
                    f"SELECT {column} FROM customer_user"  # noqa: S608 — validated identifier
                    " WHERE LOWER(email) = :e AND valid_id = 1"
                ),
                {"e": email},
            )
        ).all()
    except Exception as exc:  # noqa: BLE001 — misconfigured column must not break ingest
        logger.warning("smime_fetch_column_failed", column=column, error=str(exc))
        await session.rollback()
        return []
    out = []
    for (value,) in rows:
        data = _as_bytes(value)
        if data:
            out.append(data)
    return out


def _ldap_connection(settings: Settings) -> Any:
    """Bound ldap3 connection to the customer directory (tests patch this)."""
    import ldap3

    server = ldap3.Server(
        settings.customer_ldap_host,
        port=settings.customer_ldap_port,
        use_ssl=settings.customer_ldap_use_ssl,
        get_info=ldap3.NONE,
    )
    conn = ldap3.Connection(
        server,
        user=settings.customer_ldap_bind_dn or None,
        password=settings.customer_ldap_bind_password or None,
        auto_bind=False,
    )
    if settings.customer_ldap_use_starttls:
        conn.open()
        conn.start_tls()
    if not conn.bind():
        raise CryptoError(f"customer LDAP bind failed: {conn.result}")
    return conn


def _ldap_values_sync(settings: Settings, email: str) -> list[bytes]:
    from ldap3.utils.conv import escape_filter_chars

    attr = settings.customer_ldap_smime_attr
    filt = f"({settings.customer_ldap_email_attr}={escape_filter_chars(email)})"
    if settings.customer_ldap_always_filter:
        filt = f"(&{filt}{settings.customer_ldap_always_filter})"
    conn = _ldap_connection(settings)
    try:
        if not conn.search(
            search_base=settings.customer_ldap_base_dn,
            search_filter=filt,
            attributes=[attr],
        ):
            return []
        out: list[bytes] = []
        for entry in conn.response or []:
            attrs = entry.get("raw_attributes") or {}
            for key, values in attrs.items():
                if key.split(";", 1)[0].lower() != attr.lower():
                    continue
                for value in values or []:
                    data = _as_bytes(value)
                    if data:
                        out.append(data)
        return out
    finally:
        conn.unbind()


def ldap_source_enabled(settings: Settings) -> bool:
    return bool(settings.customer_ldap_enabled and settings.customer_ldap_host)


async def customer_certificates(
    session: AsyncSession, settings: Settings, email: str
) -> list[bytes]:
    """Raw ``UserSMIMECertificate`` values of the customer users with *email*."""
    found: list[bytes] = []
    if settings.customer_smime_cert_column:
        found.extend(await _column_values(session, settings.customer_smime_cert_column, email))
    if ldap_source_enabled(settings) and settings.customer_ldap_smime_attr:
        try:
            found.extend(await asyncio.to_thread(_ldap_values_sync, settings, email))
        except Exception as exc:  # noqa: BLE001 — directory down must not break ingest
            logger.warning("smime_fetch_ldap_failed", email=email, error=str(exc))
    return found


# ------------------------------------------------------------------ fetch


@dataclass
class FetchReport:
    added: list[str] = field(default_factory=list)  # new <hash>.<n> files
    errors: list[str] = field(default_factory=list)


async def _is_customer_address(session: AsyncSession, email: str) -> bool:
    row = (
        await session.execute(
            select(CustomerUser.id)
            .where(func.lower(CustomerUser.email) == email, CustomerUser.valid_id == 1)
            .limit(1)
        )
    ).first()
    return row is not None


async def fetch_from_customer(
    session: AsyncSession,
    settings: Settings,
    config: CryptoConfig,
    email: str,
    *,
    report: FetchReport | None = None,
) -> FetchReport:
    """Znuny ``FetchFromCustomer(Search => email)``: add the customer's certificates.

    Certificates already in the store are skipped silently; broken ones are
    reported, never raised.
    """
    report = report if report is not None else FetchReport()
    address = (email or "").strip().lower()
    if not fetch_enabled(config) or not _EMAIL_RE.match(address):
        return report
    if not await _is_customer_address(session, address):
        return report
    values = await customer_certificates(session, settings, address)
    if not values:
        return report
    store = SmimeStore.from_config(config.smime)
    for value in values:
        try:
            pem = await asyncio.to_thread(convert_cert_format, value)
            info = await asyncio.to_thread(store.cert_attributes, pem)
            if await asyncio.to_thread(store.find_by_fingerprint, info.fingerprint):
                continue
            entry = await ks.add_smime_certificate(session, store, pem, user_id=1)
        except CryptoError as exc:
            report.errors.append(f"{address}: {exc}")
            logger.warning("smime_fetch_from_customer_failed", email=address, error=str(exc))
            continue
        report.added.append(entry.filename)
        logger.info("smime_fetch_from_customer_added", email=address, filename=entry.filename)
    return report


async def fetch_for_sender(
    session: AsyncSession, settings: Settings, config: CryptoConfig, from_header: str
) -> FetchReport:
    """Postmaster pre-filter: fetch for the (last) ``From`` address of an inbound mail."""
    from email.utils import getaddresses

    from tiqora.crypto.config import setting_is_valid

    if not fetch_enabled(config):
        return FetchReport()
    if not await setting_is_valid(session, PREFILTER_SETTING):
        return FetchReport()
    addresses = [addr for _name, addr in getaddresses([from_header or ""]) if addr]
    if not addresses:
        return FetchReport()
    return await fetch_from_customer(session, settings, config, addresses[-1])


async def renew_customer_certificates(
    session: AsyncSession, settings: Settings, config: CryptoConfig
) -> FetchReport:
    """``Maint::SMIME::CustomerCertificate::Renew`` for every address in the store."""
    report = FetchReport()
    if not fetch_enabled(config) or not config.smime.cert_path:
        return report
    store = SmimeStore.from_config(config.smime)
    entries = await asyncio.to_thread(store.list_entries)
    emails = sorted({e for entry in entries if entry.info for e in entry.info.emails})
    for email in emails:
        await fetch_from_customer(session, settings, config, email, report=report)
    return report


__all__ = [
    "PREFILTER_SETTING",
    "FetchReport",
    "customer_certificates",
    "fetch_enabled",
    "fetch_for_sender",
    "fetch_from_customer",
    "renew_customer_certificates",
]
