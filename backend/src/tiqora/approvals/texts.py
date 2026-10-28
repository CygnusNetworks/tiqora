"""German and English mail texts of the approval engine (A11).

Pure text assembly, no database. German texts address the reader with "Sie". A mail carries the
request's own words (title, description, the subject type's ``describe`` lines) and never ticket
articles, the step name or comments. Optional parts (deadline, description, link, ...) are left
out when their parameter is missing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final, Literal

TextLocale = Literal["de", "en"]
KINDS: Final = (
    "request",
    "reminder",
    "escalation",
    "overdue",
    "decided",
    "cancelled",
    "forwarded",
)
#: kinds that ask for a decision (and may carry a link or the inbox hint)
ACTION_KINDS: Final = frozenset({"request", "reminder", "escalation", "overdue", "forwarded"})

_SUBJECTS: Final[dict[str, dict[str, str]]] = {
    "de": {
        "request": "Freigabe erbeten: {title}",
        "reminder": "Erinnerung: Freigabe wartet seit {age}",
        "escalation": "Eskalation: Sie können über diese Freigabe jetzt allein entscheiden",
        "overdue": "Frist überschritten: {title}",
        "decided:approved": "Freigabe erteilt: {title}",
        "decided:rejected": "Freigabe abgelehnt: {title}",
        "cancelled": "Freigabe zurückgezogen: {title}",
        "forwarded": "Freigabe an Sie weitergeleitet von {name}",
    },
    "en": {
        "request": "Approval requested: {title}",
        "reminder": "Reminder: approval waiting for {age}",
        "escalation": "Escalation: you can now decide on this approval alone",
        "overdue": "Deadline passed: {title}",
        "decided:approved": "Approval granted: {title}",
        "decided:rejected": "Approval rejected: {title}",
        "cancelled": "Approval withdrawn: {title}",
        "forwarded": "Approval forwarded to you by {name}",
    },
}
_INTROS: Final[dict[str, dict[str, str]]] = {
    "de": {
        "request": "Es liegt eine Freigabe für Sie bereit: {title}",
        "request:requester": "{requester} bittet Sie um eine Freigabe: {title}",
        "reminder": "Die folgende Freigabe wartet auf Ihre Entscheidung: {title}",
        "escalation": "Die folgende Freigabe wurde an Sie eskaliert: {title}",
        "overdue": "Die Frist der folgenden Freigabe ist überschritten: {title}",
        "decided:approved": "Die folgende Freigabe wurde erteilt: {title}",
        "decided:rejected": "Die folgende Freigabe wurde abgelehnt: {title}",
        "cancelled": "Die folgende Freigabe wurde zurückgezogen, Sie müssen nichts tun: {title}",
        "forwarded": "{name} hat Ihnen die folgende Freigabe weitergeleitet: {title}",
    },
    "en": {
        "request": "An approval is waiting for you: {title}",
        "request:requester": "{requester} asks you for an approval: {title}",
        "reminder": "The following approval is waiting for your decision: {title}",
        "escalation": "The following approval was escalated to you: {title}",
        "overdue": "The deadline of the following approval has passed: {title}",
        "decided:approved": "The following approval was granted: {title}",
        "decided:rejected": "The following approval was rejected: {title}",
        "cancelled": "The following approval was withdrawn, nothing is left to do: {title}",
        "forwarded": "{name} forwarded the following approval to you: {title}",
    },
}
_PARTS: Final[dict[str, dict[str, str]]] = {
    "de": {
        "greeting": "Guten Tag,",
        "substitute": "Sie erhalten diese Nachricht in Vertretung für {substitute_for}.",
        "deadline": "Frist: {deadline}",
        "link": "Freigeben oder ablehnen: {link}",
        "expires": "Der Link ist bis {expires} gültig und funktioniert einmal.",
        "inbox": "Sie finden die Freigabe unter „Meine Freigaben“: {inbox_url}",
        "inbox_plain": "Sie finden die Freigabe in Tiqora unter „Meine Freigaben“.",
        "first_wins": "Die erste Entscheidung zählt.",
        "footer": (
            "Diese Nachricht wurde automatisch erstellt. Eine Antwort darauf entscheidet nichts."
        ),
    },
    "en": {
        "greeting": "Hello,",
        "substitute": "You receive this message as a substitute for {substitute_for}.",
        "deadline": "Deadline: {deadline}",
        "link": "Approve or reject: {link}",
        "expires": "The link is valid until {expires} and works once.",
        "inbox": "You can find the approval under “My approvals”: {inbox_url}",
        "inbox_plain": "You can find the approval in Tiqora under “My approvals”.",
        "first_wins": "The first decision counts.",
        "footer": "This message was created automatically. A reply to it does not decide anything.",
    },
}
_RULES: Final[dict[str, dict[str, str]]] = {
    "de": {
        "any": "Es genügt eine Freigabe",
        "all": "Alle Freigeber müssen zustimmen",
        "quorum": "Mindestens {quorum} Freigaben",
        "majority": "Mehrheit der Freigeber",
    },
    "en": {
        "any": "One approval is enough",
        "all": "All approvers must agree",
        "quorum": "At least {quorum} approvals",
        "majority": "Majority of the approvers",
    },
}


def text_locale(raw: str | None) -> TextLocale:
    """``de``, ``de_DE``, ``de-AT`` -> ``de``; ``en``, ``en_GB`` -> ``en``.

    Unset or unsupported languages give the bundled default, German.
    """
    base = re.split(r"[-_]", (raw or "").strip().lower(), maxsplit=1)[0]
    return "en" if base == "en" else "de"


def _lang(locale: str) -> TextLocale:
    return "de" if locale == "de" else "en"


def rule_text(rule: str, quorum: int | None, locale: str) -> str:
    """The sentence naming the step's rule."""
    template = _RULES[_lang(locale)][rule]
    return template.format(quorum=quorum if quorum is not None else 1)


def rule_line(rule: str, quorum: int | None, locale: str) -> str:
    """:func:`rule_text`; for ``any`` it adds that the first decision counts."""
    sentence = rule_text(rule, quorum, locale)
    if rule == "any":
        return f"{sentence}. {_PARTS[_lang(locale)]['first_wins']}"
    return sentence


def age_text(delta: timedelta, locale: str) -> str:
    """How long something waits: ``2 Tagen`` / ``3 Stunden`` (after "seit"), ``2 days``."""
    hours = int(delta.total_seconds() // 3600)
    days = hours // 24
    de = _lang(locale) == "de"
    if days >= 1:
        return (
            (f"{days} Tag" if days == 1 else f"{days} Tagen")
            if de
            else (f"{days} day" if days == 1 else f"{days} days")
        )
    if hours >= 1:
        return (
            (f"{hours} Stunde" if hours == 1 else f"{hours} Stunden")
            if de
            else (f"{hours} hour" if hours == 1 else f"{hours} hours")
        )
    return "weniger als einer Stunde" if de else "less than an hour"


def moment_text(value: datetime, locale: str) -> str:
    """A naive-UTC datetime as ``27.10.2026 14:30 UTC`` (de) / ``2026-10-27 14:30 UTC`` (en)."""
    pattern = "%d.%m.%Y %H:%M UTC" if _lang(locale) == "de" else "%Y-%m-%d %H:%M UTC"
    return value.strftime(pattern)


def render(
    kind: str,
    locale: str,
    *,
    title: str,
    requester: str | None = None,
    rule: str | None = None,
    deadline: str | None = None,
    description: str | None = None,
    lines: Sequence[str] = (),
    link: str | None = None,
    expires: str | None = None,
    inbox_url: str | None = None,
    age: str | None = None,
    name: str | None = None,
    status: str = "approved",
    substitute_for: str | None = None,
    inbox_hint: bool = True,
) -> tuple[str, str]:
    """``(subject, body)`` of one mail. Raises ``KeyError`` for an unknown *kind*.

    *rule* is a finished sentence (:func:`rule_line`), *deadline*/*expires* are finished
    moments (:func:`moment_text`). A *link* wins over *inbox_url*; without either an action mail
    still says where the approval is unless *inbox_hint* is off (a customer without portal).
    ``decided`` reads *status* (``approved``/``rejected``).
    """
    lang = _lang(locale)
    if kind not in KINDS:
        raise KeyError(kind)
    key = (
        f"{kind}:{'rejected' if status == 'rejected' else 'approved'}"
        if kind == "decided"
        else kind
    )
    values = {
        "title": title,
        "requester": requester or "",
        "age": age or "",
        "name": name or "",
    }
    subject = " ".join(_SUBJECTS[lang][key].format(**values).split())
    intro_key = "request:requester" if kind == "request" and requester else key
    parts = _PARTS[lang]
    blocks: list[str] = [parts["greeting"]]
    if substitute_for:
        blocks.append(parts["substitute"].format(substitute_for=substitute_for))
    blocks.append(_INTROS[lang][intro_key].format(**values))
    facts: list[str] = []
    if rule and kind != "cancelled" and kind != "decided":
        facts.append(rule)
    if deadline and kind in ACTION_KINDS:
        facts.append(parts["deadline"].format(deadline=deadline))
    if facts:
        blocks.append("\n".join(facts))
    if description:
        blocks.append(description.strip())
    if lines:
        blocks.append("\n".join(lines))
    if kind in ACTION_KINDS:
        if link:
            action = [parts["link"].format(link=link)]
            if expires:
                action.append(parts["expires"].format(expires=expires))
            blocks.append("\n".join(action))
        elif inbox_url:
            blocks.append(parts["inbox"].format(inbox_url=inbox_url))
        elif inbox_hint:
            blocks.append(parts["inbox_plain"])
    blocks.append(parts["footer"])
    return subject, "\n\n".join(blocks) + "\n"
