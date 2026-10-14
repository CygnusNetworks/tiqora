"""Release notes and upgrade notes of v0.19.0 state the operator facts of REL-NOTES.

- nginx ``location /scim/`` only for proxies that list their locations (``location /`` forwards it);
- link types are written at the first start, not by a migration;
- compose ``service_healthy`` is recommended, not required;
- the ai-worker is rolled out with api, worker and mcp.
"""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
NOTES = (DOCS / "releases" / "v0.19.0.md").read_text(encoding="utf-8")
DEPLOY = (DOCS / "deployment.md").read_text(encoding="utf-8")
UPGRADE = DEPLOY[DEPLOY.index("## Upgrading to v0.19.0") : DEPLOY.index("## Reverse proxy sketch")]
SCIM = DEPLOY[DEPLOY.index("### SCIM provisioning") : DEPLOY.index("Release checklist: `/scim/`")]


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_scim_location_only_for_proxies_that_list_locations() -> None:
    for text in (NOTES, UPGRADE, SCIM):
        flat = _flat(text)
        assert "lists its locations explicitly" in flat or "list their locations" in flat
    assert "two infrastructure changes" not in NOTES
    assert "nginx in front of production does" not in _flat(DEPLOY)
    assert "Deploy blockers" not in SCIM


def test_link_types_written_at_first_start_not_by_the_migration() -> None:
    assert "written at the first start, not by a migration" in _flat(NOTES)
    assert "Link types are written at the first start, not by a migration" in _flat(UPGRADE)
    assert "seeded catalog and link-type rows" not in _flat(UPGRADE)


def test_service_healthy_recommended_not_required() -> None:
    assert "recommended, not required" in _flat(NOTES)
    assert "(recommended, not required)" in _flat(UPGRADE)
    assert "must therefore start after the api" not in _flat(UPGRADE)


def test_ai_worker_rolled_out_with_the_others() -> None:
    assert "ai-worker moves together with api, worker and mcp" in _flat(NOTES)
    assert "Roll out `tiqora-ai-worker` together with api, worker and mcp" in _flat(UPGRADE)
