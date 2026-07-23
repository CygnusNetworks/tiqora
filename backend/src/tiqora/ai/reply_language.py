"""Reply-language detection (plan block 3) — data-driven, no hardcoded
language bias.

Unlike the ``ticket-ai-agent`` predecessor (which hardcoded "DE/EN, default
German"), this module has no built-in preferred language: callers must
always pass an explicit ``default`` (the queue policy's
``reply_language_default``), and detection only ever chooses among the
``candidates`` a caller supplies. :data:`LANGUAGE_PROFILES` is a plain data
table of stopword sets — extending it to another language needs no code
change, only a new dict entry.

Detection runs **once per agent run**, on the ticket title plus the most
recent customer article body (never per-article) — a single binding
"reply language" line goes into the model context, see
:mod:`tiqora.ai.runtime`.
"""

from __future__ import annotations

import re

# Small, deliberately generic function-word profiles (articles, pronouns,
# conjunctions, common verbs/prepositions) — not exhaustive, just enough to
# separate the two languages reliably on short texts. Extend/add languages
# here; nothing else in this module needs to change.
LANGUAGE_PROFILES: dict[str, frozenset[str]] = {
    "de": frozenset(
        {
            "der",
            "die",
            "das",
            "den",
            "dem",
            "des",
            "ein",
            "eine",
            "einer",
            "eines",
            "einem",
            "einen",
            "und",
            "oder",
            "aber",
            "ich",
            "du",
            "er",
            "sie",
            "es",
            "wir",
            "ihr",
            "mein",
            "meine",
            "ihre",
            "ihren",
            "ist",
            "sind",
            "war",
            "waren",
            "habe",
            "haben",
            "hatte",
            "wird",
            "werden",
            "kann",
            "können",
            "muss",
            "müssen",
            "nicht",
            "kein",
            "keine",
            "für",
            "mit",
            "von",
            "vom",
            "bei",
            "beim",
            "zu",
            "zum",
            "zur",
            "auf",
            "aus",
            "im",
            "in",
            "an",
            "am",
            "auch",
            "noch",
            "schon",
            "sehr",
            "bitte",
            "danke",
            "vielen",
            "freundlichen",
            "grüßen",
            "grüße",
            "guten",
            "tag",
            "hallo",
            "wieder",
            "immer",
            "hier",
            "da",
        }
    ),
    "en": frozenset(
        {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "i",
            "you",
            "he",
            "she",
            "it",
            "we",
            "they",
            "my",
            "your",
            "his",
            "her",
            "their",
            "is",
            "are",
            "was",
            "were",
            "have",
            "has",
            "had",
            "will",
            "would",
            "can",
            "could",
            "must",
            "should",
            "not",
            "no",
            "for",
            "with",
            "from",
            "by",
            "to",
            "of",
            "on",
            "at",
            "in",
            "into",
            "also",
            "still",
            "already",
            "very",
            "please",
            "thanks",
            "thank",
            "regards",
            "best",
            "hello",
            "hi",
            "dear",
            "again",
            "always",
            "here",
            "there",
            "this",
            "that",
        }
    ),
}

_MIN_SCORE = 2

# Noise stripped before tokenization: e-mail addresses, URLs, and
# alphanumeric ID/reference-code tokens (e.g. "z75363") that carry no
# language signal and would otherwise pollute the token stream.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://\S+")
_ALPHANUMERIC_ID_RE = re.compile(r"\b(?=[a-zA-Z]*\d)(?=\d*[a-zA-Z])[a-zA-Z0-9]{4,}\b")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _strip_noise(text: str) -> str:
    text = _EMAIL_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _ALPHANUMERIC_ID_RE.sub(" ", text)
    return text


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(_strip_noise(text))]


def _score(tokens: list[str], profile: frozenset[str]) -> int:
    return sum(1 for t in tokens if t in profile)


def detect_reply_language(
    title: str | None,
    latest_customer_body: str | None,
    *,
    candidates: list[str],
    default: str,
) -> str:
    """Pick the best-matching language among ``candidates`` for ``title`` +
    ``latest_customer_body``; falls back to ``default`` when no candidate
    reaches the minimum stopword-match score (short/ambiguous text)."""
    tokens = _tokenize(f"{title or ''} {latest_customer_body or ''}")
    if not tokens or not candidates:
        return default
    best_lang = default
    best_score = _MIN_SCORE - 1
    for lang in candidates:
        profile = LANGUAGE_PROFILES.get(lang)
        if profile is None:
            continue
        score = _score(tokens, profile)
        if score > best_score:
            best_score = score
            best_lang = lang
    if best_score < _MIN_SCORE:
        return default
    return best_lang


__all__ = ["LANGUAGE_PROFILES", "detect_reply_language"]
