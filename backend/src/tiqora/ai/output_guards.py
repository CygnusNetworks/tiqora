"""Output guards for model-proposed customer messages (plan C #7).

Applied inside the tool executor on ``propose_customer_message`` before the
runtime maps draft/send. Rejects oversized or link-flooded bodies so a
prompt-injected model cannot dump large or link-heavy payloads to a customer
(or into a draft that an agent might accept without noticing).
"""

from __future__ import annotations

import re

# Soft product limits — generous for real support replies, tight enough to
# stop bulk exfil dumps.
MAX_CUSTOMER_BODY_CHARS = 20_000
MAX_CUSTOMER_SUBJECT_CHARS = 500
MAX_CUSTOMER_BODY_LINKS = 15

_LINK_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Obvious secret/exfil phrases (heuristic only; not a content filter).
_SUSPICIOUS_PATTERNS = (
    re.compile(r"\bapi[_-]?key\b\s*[:=]", re.IGNORECASE),
    re.compile(r"\bbearer\s+[a-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class CustomerMessageGuardError(ValueError):
    """Raised when a proposed customer message fails an output guard."""


def validate_customer_message(*, kind: str, subject: str, body: str) -> None:
    """Raise :class:`CustomerMessageGuardError` if the proposal is not safe
    enough to hand to the draft/send path."""
    if kind not in ("reply", "clarify"):
        raise CustomerMessageGuardError(f"Invalid message kind: {kind!r}")
    if not isinstance(body, str) or not body.strip():
        raise CustomerMessageGuardError("Customer message body must be non-empty")
    if len(body) > MAX_CUSTOMER_BODY_CHARS:
        raise CustomerMessageGuardError(
            f"Customer message body exceeds {MAX_CUSTOMER_BODY_CHARS} characters"
        )
    if subject and len(subject) > MAX_CUSTOMER_SUBJECT_CHARS:
        raise CustomerMessageGuardError(
            f"Customer message subject exceeds {MAX_CUSTOMER_SUBJECT_CHARS} characters"
        )
    link_count = len(_LINK_RE.findall(body))
    if link_count > MAX_CUSTOMER_BODY_LINKS:
        raise CustomerMessageGuardError(
            f"Customer message body has too many links ({link_count} > {MAX_CUSTOMER_BODY_LINKS})"
        )
    for pattern in _SUSPICIOUS_PATTERNS:
        if pattern.search(body) or (subject and pattern.search(subject)):
            raise CustomerMessageGuardError(
                "Customer message body matches a blocked secret/exfiltration pattern"
            )


__all__ = [
    "MAX_CUSTOMER_BODY_CHARS",
    "MAX_CUSTOMER_BODY_LINKS",
    "MAX_CUSTOMER_SUBJECT_CHARS",
    "CustomerMessageGuardError",
    "validate_customer_message",
]
