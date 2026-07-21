"""Reply/forward subject and quoted-body helpers.

Ports the parts of Znuny's quoting behaviour relevant to Tiqora's reply
dialog:

- Subject dedup/prefixing mirrors ``Kernel::System::Ticket::TicketSubjectClean``
  + ``TicketSubjectBuild`` (``znuny-6.5.22/Kernel/System/Ticket.pm``): strip
  any existing ``Re:``/``Aw:``/``Antw:``/``Fwd:``/``Wg:`` prefixes (repeated,
  case-insensitive — Znuny's default ``Ticket::SubjectRe``/``SubjectFwd``
  plus common German MUA variants) before prepending exactly one. Also strip
  a configured ``[<hook><divider><tn>]`` bracket tag so re-building never
  accumulates tags.
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


def strip_ticket_hook(subject: str | None, *, hook: str, divider: str) -> str:
    """Remove every ``[<hook><divider><digits>]`` tag (case-insensitive).

    Mirrors Znuny's ``TicketSubjectClean`` tag strip so re-building never
    doubles the ticket number hook. Digits are any run of 1+ digits (tn
    format is generator-specific; stripping is deliberately permissive).
    """
    s = subject or ""
    if not hook:
        return s.strip()
    # Tag may appear leading, trailing, or embedded; strip all occurrences.
    tag_re = re.compile(
        r"\s*\[\s*" + re.escape(hook) + re.escape(divider) + r"\d+\s*\]\s*",
        re.IGNORECASE,
    )
    cleaned = tag_re.sub(" ", s)
    # Collapse whitespace left by mid-string removals.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def clean_subject(
    subject: str | None,
    *,
    hook: str | None = None,
    divider: str | None = None,
) -> str:
    """Strip repeated Re:/Aw:/Antw:/Fwd:/Wg: prefixes (and optional hook tag)."""
    s = (subject or "").strip()
    if hook is not None:
        s = strip_ticket_hook(s, hook=hook, divider=divider or "")
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


def build_ticket_subject(
    subject: str | None,
    *,
    hook: str,
    divider: str,
    tn: str,
    subject_format: str = "Left",
    add_re: bool = False,
    add_fwd: bool = False,
) -> str:
    """Idempotent subject with optional Re:/Fwd: and ticket-number hook tag.

    1. Strip any existing ``[<hook><divider><tn>]`` tag for *hook*.
    2. Optionally apply exactly one ``Re:`` / ``Fwd:`` via the existing helpers
       (which also strip repeated reply/forward prefixes).
    3. Place the tag per *subject_format*:
       - ``Left`` → ``[<hook><divider><tn>] <subject>``
       - ``Right`` → ``<subject> [<hook><divider><tn>]``
       - ``None`` → no tag

    Never double-tags or double-Re-prefixes. When *add_re*/*add_fwd* are both
    false the subject text is left as-is after the hook strip (agent composers
    already supply ``Re:``).
    """
    base = strip_ticket_hook(subject, hook=hook, divider=divider)
    if add_re and add_fwd:
        # Prefer Re: when both requested (should not happen in practice).
        base = build_reply_subject(base)
    elif add_re:
        base = build_reply_subject(base)
    elif add_fwd:
        base = build_forward_subject(base)
    else:
        base = (base or "").strip()

    fmt = (subject_format or "Left").strip()
    if fmt.lower() == "none" or not tn:
        return base
    tag = f"[{hook}{divider}{tn}]"
    if fmt.lower() == "right":
        return f"{base} {tag}".strip() if base else tag
    # Default / Left
    return f"{tag} {base}".strip() if base else tag


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
