"""Origin handling for the web-chat widget (allowed websites)."""

from __future__ import annotations

from collections.abc import Iterable


def validate_origins(values: Iterable[str]) -> list[str]:
    """Normalise and validate the allowed origins.

    Placeholder: the real validation (scheme/host/port normalisation, wildcard
    rejection) arrives with Task 3.
    """
    return list(values)
