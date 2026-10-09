"""Cut the quoted earlier message off a customer e-mail body.

A reply to our own (German) mail arrives as ``new text`` + ``On <date>, X
wrote:`` + the whole earlier mail, each line prefixed with ``>``. For the
model and for the reply-language detector only the new text is the
customer's voice: the quote is our own earlier answer, so it adds nothing but
tokens — and it votes for the *previous* language (prod, ticket
2026000000000001: an English follow-up under a quoted German reply was
detected as German).

Deliberately conservative: only the *trailing* quote is removed (attribution
line + a body that is predominantly ``>`` lines, or an "Original Message"
separator). Interleaved answers (``> question`` / ``answer`` / ``> question``)
stay untouched because the customer's answers are in the middle of the quote.
"""

from __future__ import annotations

import re

# "On Fri, 9 Oct 2026 at 09:26 X <x@y>\nwrote:" / "Am 09.10.2026 um 09:26
# schrieb X:" — the attribution may be wrapped over up to three lines.
_ATTRIBUTION_RE = re.compile(
    r"^[ \t]*(?:On|Am)\b(?:[^\n]*\n){0,2}?[^\n]*\b(?:wrote|schrieb)\b[^\n]*:[ \t]*$",
    re.MULTILINE,
)
_SEPARATOR_RE = re.compile(
    r"^[ \t]*-{2,}\s*(?:Original Message|Ursprüngliche Nachricht|Urspr\. Nachricht)\s*-{2,}[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
_MIN_TRAILING_QUOTE_LINES = 3
REMOVED_MARKER = "[quoted earlier message removed]"


def _mostly_quoted(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return True
    quoted = sum(1 for ln in lines if ln.lstrip().startswith(">"))
    return quoted / len(lines) >= 0.8


def strip_trailing_quote(body: str | None) -> str:
    """``body`` without its trailing quoted reply chain (see module doc).

    Returns the text unchanged when no quote is recognised, and never returns
    an empty string for a body that had content only inside the quote (a pure
    forward): then the original is kept."""
    if not body:
        return body or ""
    text = body.replace("\r\n", "\n")
    cut: int | None = None
    sep = _SEPARATOR_RE.search(text)
    if sep:
        cut = sep.start()
    for m in _ATTRIBUTION_RE.finditer(text):
        if _mostly_quoted(text[m.end() :]):
            cut = m.start() if cut is None else min(cut, m.start())
            break
    if cut is None:
        # No attribution line: a trailing run of ">" lines still is a quote.
        lines = text.split("\n")
        end = len(lines)
        while end > 0 and not lines[end - 1].strip():
            end -= 1
        start = end
        while start > 0 and (
            lines[start - 1].lstrip().startswith(">") or not lines[start - 1].strip()
        ):
            start -= 1
        run = [ln for ln in lines[start:end] if ln.strip()]
        if len(run) >= _MIN_TRAILING_QUOTE_LINES:
            cut = len("\n".join(lines[:start]))
    if cut is None:
        return body
    kept = text[:cut].rstrip()
    return kept if kept.strip() else body


__all__ = ["REMOVED_MARKER", "strip_trailing_quote"]
