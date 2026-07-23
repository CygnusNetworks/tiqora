"""PII masking — defense-in-depth for structured identifiers (plan §3.7).

Ports the ``PiiMapper`` concept from ``ticket-ai-agent`` nearly 1:1: mask
structured identifiers (e-mail, phone, MAC, IPv4/IPv6) with stable
placeholder tokens before text reaches the LLM, and reverse the mapping
before a tool call touches the real MCP/domain layer or before a final
customer-facing text is persisted/sent.

This is **not** a complete PII filter — free-text names without a
structured pattern are a documented limitation (plan §3.7); the primary
privacy control is provider selection (EU hosting, no-training AVV), not
this module. One :class:`PiiMapper` instance is scoped to a single agent
run so tokens stay stable across the whole tool loop.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

# Order matters: each pattern is applied to the *already-partially-masked*
# text, so a looser pattern applied later never re-matches an already
# replaced token (which looks like ``[EMAIL_1]``, not an email/phone/etc).
# MAC must run before IPv6 (a MAC's six ``XX:`` groups would otherwise be a
# valid IPv6 group prefix); IPv6 before IPv4-looking substrings is irrelevant
# since the shapes don't overlap; phone runs last because its char class is
# the loosest.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
# {0,4} (not {1,4}) per group so "::" (zero-compression) still matches.
_IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b")
_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
# Loose phone matcher: a run of 8+ digits allowing spaces/dots/dashes/slashes/
# parens (e.g. "030/1234567"), optionally prefixed with '+'. Deliberately
# conservative (min length) so it does not swallow ticket numbers or short
# reference codes. Dates (23.07.2026, 23. 07. 2026, 2026-07-23) also fit this
# loose shape, so a validator rejects them below rather than tightening the
# char class itself.
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s()./-]{6,}\d)(?!\w)")

# A full match (trimmed) shaped like a date — rejected by _validate_phone so
# dates survive unmasked. Covers dd.mm.yyyy / dd/mm/yyyy (with or without a
# space after the separator) and ISO yyyy-mm-dd.
_DATE_LIKE_RE = re.compile(r"\A\d{1,2}[./]\s?\d{1,2}[./]\s?\d{2,4}\Z|\A\d{4}-\d{2}-\d{2}\Z")


def _validate_phone(value: str) -> bool:
    """A PHONE candidate is only masked when it has enough digits to plausibly
    be a phone number AND is not shaped like a date."""
    if sum(1 for c in value if c.isdigit()) < 7:
        return False
    return not _DATE_LIKE_RE.match(value.strip())


# (kind, compiled pattern, optional validator) in application order. A
# candidate match is only masked when the validator (if given) returns True
# for the matched text; otherwise it is left untouched in the output.
_PATTERNS: tuple[tuple[str, re.Pattern[str], Callable[[str], bool] | None], ...] = (
    ("EMAIL", _EMAIL_RE, None),
    ("MAC", _MAC_RE, None),
    ("IPV6", _IPV6_RE, None),
    ("IPV4", _IPV4_RE, None),
    ("PHONE", _PHONE_RE, _validate_phone),
)


class PiiMapper:
    """Best-effort, reversible masking of structured identifiers.

    One instance per agent run (tokens must stay stable within a run so the
    model can refer back to a masked value across tool-loop turns, and so
    :meth:`unmask` can restore the original in a final draft/tool argument).
    """

    def __init__(
        self,
        *,
        never_mask: frozenset[str] | set[str] | None = None,
        known_names: Sequence[str] | None = None,
    ) -> None:
        self._never_mask = frozenset(v for v in (never_mask or ()) if v)
        self._token_by_value: dict[str, str] = {}
        self._value_by_token: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        # Data-driven name masking (plan §3.7 gap): only names explicitly
        # provided by the caller are ever masked — no NER, no heuristics.
        # Longest-first so e.g. "Anna-Lena Meyer" wins over a bare "Meyer"
        # entry for the same person (Python's re tries alternatives in the
        # given order at a given start position and stops at the first
        # match, so precedence follows list order, not match length).
        names = sorted(
            {n.strip() for n in (known_names or ()) if n and len(n.strip()) >= 3},
            key=len,
            reverse=True,
        )
        self._name_pattern = (
            re.compile("|".join(rf"\b{re.escape(n)}\b" for n in names), re.IGNORECASE)
            if names
            else None
        )

    def _token_for(self, kind: str, value: str) -> str:
        existing = self._token_by_value.get(value)
        if existing is not None:
            return existing
        self._counters[kind] = self._counters.get(kind, 0) + 1
        token = f"[{kind}_{self._counters[kind]}]"
        self._token_by_value[value] = token
        self._value_by_token[token] = value
        return token

    def mask(self, text: str | None) -> str:
        """Replace structured identifiers with stable ``[KIND_n]`` tokens.

        Values in ``never_mask`` (exact string match against the matched
        span) are left untouched — e.g. the ticket number, the customer_id
        itself (still needed by MCP tools), configured infrastructure
        IPs/domains.
        """
        if not text:
            return text or ""
        result = text
        for kind, pattern, validate in _PATTERNS:

            def _replace(
                match: re.Match[str],
                _kind: str = kind,
                _validate: Callable[[str], bool] | None = validate,
            ) -> str:
                value = match.group(0)
                if value in self._never_mask:
                    return value
                if _validate is not None and not _validate(value):
                    return value
                return self._token_for(_kind, value)

            result = pattern.sub(_replace, result)
        if self._name_pattern is not None:

            def _replace_name(match: re.Match[str]) -> str:
                value = match.group(0)
                if value in self._never_mask:
                    return value
                return self._token_for("NAME", value)

            result = self._name_pattern.sub(_replace_name, result)
        return result

    def unmask(self, text: str | None) -> str:
        """Reverse :meth:`mask` — restore original values for placeholder tokens."""
        if not text:
            return text or ""
        if not self._value_by_token:
            return text

        token_re = re.compile("|".join(re.escape(t) for t in self._value_by_token))
        return token_re.sub(lambda m: self._value_by_token[m.group(0)], text)

    @property
    def mapping(self) -> dict[str, str]:
        """Read-only ``{token: original_value}`` snapshot (never persist raw)."""
        return dict(self._value_by_token)


__all__ = ["PiiMapper"]
