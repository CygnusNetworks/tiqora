"""Reply/forward subject and quoted-body helpers.

Ports the parts of Znuny's quoting behaviour relevant to Tiqora's reply
dialog:

- Subject dedup/prefixing mirrors ``Kernel::System::Ticket::TicketSubjectClean``
  + ``TicketSubjectBuild`` (``znuny-6.5.22/Kernel/System/Ticket.pm``): strip
  any existing ``Re:``/``Aw:``/``Antw:``/``Fwd:``/``Wg:`` prefixes (repeated,
  case-insensitive — Znuny's default ``Ticket::SubjectRe``/``SubjectFwd``
  plus common German MUA variants) before prepending exactly one.
- Quoted body mirrors the attribution + ``> ``-quoting Znuny's
  ``Kernel::Output::HTML::Layout::_Quote`` / ``TemplateGenerator`` produce
  for plaintext bodies (``On <date>, <from> wrote:`` + each line prefixed).
  Tiqora simplifies to plaintext-only quoting (see docs/architecture.md) —
  Znuny additionally supports HTML-blockquote quoting for rich-text replies,
  which is out of scope here.
"""

from __future__ import annotations

import re
from datetime import datetime

_REPLY_PREFIX_RE = re.compile(r"^\s*(re|aw|antw|fwd|wg)\s*:\s*", re.IGNORECASE)


def clean_subject(subject: str | None) -> str:
    """Strip repeated Re:/Aw:/Antw:/Fwd:/Wg: prefixes from a subject."""
    s = (subject or "").strip()
    while True:
        stripped = _REPLY_PREFIX_RE.sub("", s)
        if stripped == s:
            return s
        s = stripped.strip()


def build_reply_subject(subject: str | None) -> str:
    """``Re: `` + cleaned subject (never double-prefixed)."""
    return f"Re: {clean_subject(subject)}"


def build_forward_subject(subject: str | None) -> str:
    """``Fwd: `` + cleaned subject (never double-prefixed)."""
    return f"Fwd: {clean_subject(subject)}"


def quote_plaintext_body(
    body: str,
    *,
    from_address: str | None,
    sent_at: datetime | None,
) -> str:
    """Attribution line + ``> ``-prefixed quoted body (plaintext only).

    Mirrors Znuny's ``On <date>, <from> wrote:`` attribution followed by the
    quoted text, each line prefixed with ``> ``. HTML bodies must be
    converted to plaintext by the caller before quoting (Tiqora does not
    quote HTML directly).
    """
    who = from_address or "unknown sender"
    when = sent_at.strftime("%Y-%m-%d %H:%M") if sent_at else "an earlier date"
    attribution = f"On {when}, {who} wrote:"
    lines = (body or "").splitlines() or [""]
    quoted = "\n".join(f"> {line}" if line else ">" for line in lines)
    return f"{attribution}\n{quoted}"


def html_to_plaintext(html_body: str) -> str:
    """Very small HTML→plaintext fallback for quoting HTML article bodies.

    Not a full renderer (Tiqora already has ``article_html.py`` for safe
    *display* of HTML bodies) — this only needs to produce reasonable quoted
    plaintext: strip tags, decode a handful of common entities, collapse
    block-level tags to newlines.
    """
    import html as _html

    text = html_body or ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)</tr\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    # Collapse 3+ consecutive blank lines to a single blank line.
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        out.append(line)
    return "\n".join(out).strip("\n")
