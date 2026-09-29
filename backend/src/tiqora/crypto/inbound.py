"""Inbound crypto for postmaster mail: decrypt + verify via the MIME-tree walk.

Wired into the postmaster pipeline (:mod:`tiqora.channels.email.pipeline`)
before the article is built. A no-op unless PGP or S/MIME is enabled
(SysConfig ``PGP``/``SMIME`` or ``TIQORA_CRYPTO_*_ENABLED``). A
decrypt/verify failure never blocks delivery: the article is still created
from whatever could be parsed and the failure is recorded as its security
status (Znuny's ``ArticleCheck::PGP``/``::SMIME`` annotate rather than
reject).

The heavy lifting — PGP/MIME, inline PGP, S/MIME detached/opaque/enveloped,
nesting, trust — lives in :mod:`tiqora.crypto.mime_walk`. This module adds
the pipeline-facing view: the decrypted body and attachments to store *in
clear* on the article (Znuny does the same via ``ArticleUpdate`` on first
view; Tiqora does it once at ingest) and the security summary for the
``TiqoraCrypto*`` article flags.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from tiqora.channels.email.parser import ParsedAttachment, parse_email
from tiqora.config import Settings
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.mime_walk import SecurityResult, walk_message

#: Subjects mail clients put on the outside when the real one is protected.
_PLACEHOLDER_SUBJECTS = {"", "...", "…", "encrypted message", "[...]"}


@dataclass
class InboundCryptoOutcome:
    """What the pipeline applies to the parsed mail.

    ``body``/``content_type``/``attachments`` are ``None`` when the mail
    content is unchanged (only signature-checked in place, or nothing could
    be decrypted).
    """

    security: SecurityResult
    body: str | None = None
    content_type: str | None = None
    attachments: list[ParsedAttachment] | None = field(default=None)
    protected_subject: str | None = None

    def subject_for(self, outer_subject: str) -> str:
        """Outer subject, or the protected one when the outer is a placeholder."""
        if self.protected_subject and outer_subject.strip().lower() in _PLACEHOLDER_SUBJECTS:
            return self.protected_subject
        return outer_subject


def process_inbound_crypto_sync(raw: bytes, config: CryptoConfig) -> InboundCryptoOutcome | None:
    """Blocking core (gpg/openssl subprocesses). ``None`` = no crypto found / disabled."""
    outcome = walk_message(raw, config)
    if outcome.security is None:
        return None
    result = InboundCryptoOutcome(
        security=outcome.security, protected_subject=outcome.protected_subject
    )
    if outcome.content is not None:
        inner = parse_email(outcome.content)
        result.body = inner.body
        result.content_type = inner.content_type
        result.attachments = inner.attachments
    return result


async def process_inbound_crypto(
    raw: bytes, settings: Settings, config: CryptoConfig | None = None
) -> InboundCryptoOutcome | None:
    """Async wrapper: runs the walk in a worker thread.

    *config* is the SysConfig-resolved crypto config; without it the env-only
    view of *settings* applies.
    """
    cfg = config or CryptoConfig.from_settings(settings)
    if not cfg.pgp.enabled and not cfg.smime.enabled:
        return None
    return await asyncio.to_thread(process_inbound_crypto_sync, raw, cfg)


__all__ = ["InboundCryptoOutcome", "process_inbound_crypto", "process_inbound_crypto_sync"]
