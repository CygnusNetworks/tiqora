"""Release notes and upgrade notes of v0.20.0 state the operator facts of the readiness analysis."""

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
NOTES = (DOCS / "releases" / "v0.20.0.md").read_text(encoding="utf-8")
DEPLOY = (DOCS / "deployment.md").read_text(encoding="utf-8")
UPGRADE = DEPLOY[DEPLOY.index("## Upgrading to v0.20.0") : DEPLOY.index("## Upgrading to v0.19.0")]


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_public_docs_name_no_production_host_or_path() -> None:
    for text in (NOTES, UPGRADE):
        assert not re.search(r"cygnus|prod-sql", text, re.IGNORECASE)


def test_rollback_recipe_is_complete() -> None:
    upgrade = _flat(UPGRADE)
    assert "pause Watchtower" in upgrade
    assert "`TIQORA_RUN_MIGRATIONS=0` in `.env`" in upgrade
    assert "remove `TIQORA_RUN_MIGRATIONS=0`" in upgrade
    assert "**Those mails are lost**" in upgrade
    assert "`TIQORA_RUN_MIGRATIONS=0` in `.env`" in _flat(NOTES)


def test_roll_out_starts_the_api_first_and_waits_for_the_head() -> None:
    upgrade = _flat(UPGRADE)
    assert upgrade.index("up -d tiqora-api") < upgrade.index("up -d tiqora-worker")
    assert "`20261010_0085 (head)`" in upgrade


def test_notes_say_emoji_are_sanitised_and_list_the_known_issues() -> None:
    notes = _flat(NOTES)
    assert "sanitised like on tickets" in notes
    assert "HTTP 500. The original character is not kept" in notes
    for issue in (
        "`/agent/tickets/{id}` at 390 px still scrolls sideways",
        "shortcuts help cannot be reached",
        "fails permanently",
        "network error before a ticket exists",
    ):
        assert issue in notes


def test_corrected_claims_stay_corrected() -> None:
    notes = _flat(NOTES)
    assert "**only if no erasure ran since the rotation**" in notes
    assert "does not run the CMDB erasure and does not touch approval rows" in notes
    assert "after creation **in the UI**" in notes
