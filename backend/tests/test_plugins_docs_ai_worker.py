"""docs/plugins.md: AI writes run no sync hooks (R16); four processes need the plugin image."""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
DOC = (DOCS / "plugins.md").read_text(encoding="utf-8")
DEPLOY = (DOCS / "deployment.md").read_text(encoding="utf-8")


def test_ai_worker_runs_no_sync_hooks_in_docs() -> None:
    assert "| sync hooks | yes | yes | yes | – |" in DOC
    assert "run the write services, sync" not in DOC
    assert DOC.count("AI auto-replies, AI tool writes") >= 1
    assert "automatische KI-Antworten" in DOC


def test_part1_ai_worker_bullet_does_not_promise_sync_hooks() -> None:
    # Teil 1 calls sync hooks "Prüfungen"; the ai-worker runs none (R16).
    assert "Prüfungen und Kanäle bei" not in DOC
    assert "Filter für ausgehende E-Mails\n  und Kanäle bei KI-Antworten" in DOC
    assert "danach Worker und MCP-Dienst" not in DOC


def test_all_four_processes_need_the_plugin_image() -> None:
    assert "Alle drei brauchen" not in DOC
    assert "der drei Prozesse" not in DOC
    assert "`tiqora-mcp` und `tiqora-ai-worker`" in DOC
    assert "`tiqora-mcp` and `tiqora-ai-worker`" in DEPLOY
