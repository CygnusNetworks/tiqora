"""Every registered job kind and every worker daemon has its UI label in en and de."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tiqora.extensions import get_registry
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.worker.services import DAEMON_SERVICES

LOCALES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "locales"


def _lookup(tree: dict[str, Any], dotted: str) -> Any:
    cur: Any = tree
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


@pytest.mark.parametrize("lang", ["en", "de"])
def test_job_kinds_and_daemons_have_labels(lang: str) -> None:
    tree = json.loads((LOCALES / f"{lang}.json").read_text("utf-8"))
    missing = [
        h.title_key
        for h in get_registry().items(JOB_HANDLERS)
        if h.title_key and not isinstance(_lookup(tree, h.title_key), str)
    ]
    for svc in DAEMON_SERVICES:
        for leaf in ("name", "description"):
            key = f"admin.daemons.services.{svc.slug}.{leaf}"
            if not isinstance(_lookup(tree, key), str):
                missing.append(key)
    assert missing == []
