"""spaCy-based person-name extraction feeding PII name masking (plan §3.7 gap).

:class:`tiqora.ai.pii.PiiMapper` only masks names it is explicitly told
about (``known_names``) — no NER, no heuristics, by design. This module
supplies additional candidates by running two small spaCy NER pipelines
(German + English) over the *raw* (unmasked) article text, so a
third-party name mentioned in the body (e.g. "Marie Knoblich hat mir
erzählt...") can be masked even though it never appears in a From-header or
customer_user record (see :func:`tiqora.ai.context.collect_known_names`).

Both models are loaded lazily, once per process, with every pipeline
component except ``tok2vec``/``ner`` excluded — tagger/parser/lemmatizer/
attribute_ruler/morphologizer are not needed for entity recognition and
loading only ``tok2vec`` + ``ner`` cuts load time roughly in half while still
producing identical ``PER``/``PERSON`` entities (verified empirically; a
bare ``exclude=[..., "tok2vec"]`` silently disables the NER component
because it depends on tok2vec's vectors).

Defensive by design: any failure to import spaCy or load a model (missing
wheel, corrupted cache, ...) is logged once and downgrades to "no NER
candidates" rather than breaking the masking pipeline.
"""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from spacy.language import Language

logger = structlog.get_logger(__name__)

_EXCLUDE_DE = ["tagger", "morphologizer", "parser", "lemmatizer", "attribute_ruler"]
_EXCLUDE_EN = ["tagger", "parser", "lemmatizer", "attribute_ruler"]

_PERSON_LABELS = frozenset({"PER", "PERSON"})

_lock = threading.Lock()
_models: list[Language] | None = None
_load_failed = False

_HAS_DIGIT_RE = re.compile(r"\d")

# German honorifics that make a *single-token* candidate ("Frau Müller")
# credible. The small spaCy models tag ordinary capitalized German nouns
# ("Vertrauensstudenten", "Benachteiligung", "Bewohner"...) as PER far too
# often, so lone tokens are only accepted with such context; multi-token
# candidates ("Marie Knoblich") are accepted as-is.
_HONORIFIC = r"(?:Herrn?|Frau|Hr\.|Fr\.|Dr\.|Prof\.)"


def _plausible_person_name(name: str, snippet: str) -> bool:
    tokens = name.split()
    if not tokens:
        return False
    # First and last token must be capitalized; middle tokens may be
    # lowercase nobiliary particles ("von", "zu", "de").
    if tokens[0][0].islower() or tokens[-1][0].islower():
        return False
    if len(tokens) >= 2:
        return True
    return (
        re.search(
            rf"{_HONORIFIC}\s+(?:{_HONORIFIC}\s+)?{re.escape(name)}(?!\w)",
            snippet,
        )
        is not None
    )


def _load_models() -> list[Language]:
    global _models, _load_failed
    if _models is not None:
        return _models
    with _lock:
        if _models is not None:
            return _models
        if _load_failed:
            return []
        try:
            import spacy

            models = [
                spacy.load("de_core_news_sm", exclude=_EXCLUDE_DE),
                spacy.load("en_core_web_sm", exclude=_EXCLUDE_EN),
            ]
        except (ImportError, OSError):
            logger.warning("ai_ner_model_load_failed", exc_info=True)
            _load_failed = True
            return []
        _models = models
        return _models


def extract_person_names(text: str, *, max_chars: int = 100_000) -> list[str]:
    """Person names found by either the German or English NER model.

    Truncates ``text`` to ``max_chars`` before running NER (spaCy pipelines
    scale with input length; a single article/attachment body should never
    need more than that to yield names). Returns a deduped (case-insensitive),
    sorted list; entries shorter than 3 characters or containing a digit are
    dropped (ticket numbers, reference codes, etc. are not names), as are
    implausible candidates (lowercase-initial words, lone tokens without an
    honorific — see :func:`_plausible_person_name`).

    Never raises: any model-loading failure yields an empty list (see
    :func:`_load_models`).
    """
    if not text:
        return []
    models = _load_models()
    if not models:
        return []
    snippet = text[:max_chars]

    names_by_lower: dict[str, str] = {}
    for nlp in models:
        doc = nlp(snippet)
        for ent in doc.ents:
            if ent.label_ not in _PERSON_LABELS:
                continue
            name = ent.text.strip()
            if len(name) < 3 or _HAS_DIGIT_RE.search(name):
                continue
            if not _plausible_person_name(name, snippet):
                continue
            names_by_lower.setdefault(name.lower(), name)
    return sorted(names_by_lower.values())


def _reset_for_tests() -> None:
    """Test-only hook to force a fresh load attempt (module-level singleton
    otherwise persists across tests in the same process)."""
    global _models, _load_failed
    with _lock:
        _models = None
        _load_failed = False


__all__ = ["extract_person_names"]
