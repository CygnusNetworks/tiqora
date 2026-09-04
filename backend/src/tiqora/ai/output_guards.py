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


# Closing salutations the model may use; anything *after* that line is the
# queue signature / AI-disclosure footer, which the mailer appends itself.
_SIGNOFF_PHRASES = {
    "best regards",
    "with best regards",
    "kind regards",
    "with kind regards",
    "regards",
    "warm regards",
    "with warm regards",
    "mit freundlichen grüßen",
    "freundliche grüße",
    "viele grüße",
    "liebe grüße",
    "beste grüße",
    "mfg",
}


def _is_signoff_line(line: str) -> bool:
    normalized = line.strip().rstrip(",:.").lower()
    if not normalized:
        return False
    if normalized in _SIGNOFF_PHRASES:
        return True
    if normalized.startswith("with ") and normalized[5:] in _SIGNOFF_PHRASES:
        return True
    return False


def strip_hallucinated_signoff(body: str) -> str:
    """Keep a trailing closing salutation; drop the signature/footer after it.

    "Best regards" / "With best regards" / "Mit freundlichen Grüßen" stay.
    Name, role, phone, ``--`` delimiter and AI-disclosure copied after that
    line are dropped — the mailer appends the real queue signature on send.
    Only the last few lines are inspected so quoted earlier mail is untouched.
    """
    lines = body.rstrip().split("\n")
    window_start = max(0, len(lines) - 12)
    signoff_at = None
    for i in range(window_start, len(lines)):
        if _is_signoff_line(lines[i]):
            signoff_at = i
    if signoff_at is None:
        return body
    return "\n".join(lines[: signoff_at + 1]).rstrip()


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
    "strip_hallucinated_signoff",
    "validate_customer_message",
]
