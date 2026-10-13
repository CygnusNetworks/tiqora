"""docs/itsm.md keeps its importer headings and every link to one of its anchors resolves."""

import re
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[2]
ITSM_MD = REPO / "docs" / "itsm.md"

OWNED_HEADINGS = (
    "## 7 Parallelbetrieb und Umstieg",
    "## Koexistenz mit Znuny",
    "### Die drei Modi",
    "### Was Sie in welchem Modus sehen",
    "### Umstellung auf Tiqora (Ablauf)",
    "### Was bleibt, was entfällt (je Znuny-Bildschirm)",
    "### Zurück zu Modus A",
    "### Bekannte Lücken",
    "## ITSM-Daten aus Znuny übernehmen",
    "## Znuny-Erweiterung TiqoraITSM",
)

_LINK = re.compile(r"itsm\.md#([^\s)\"'`\]>]+)")
_SCAN_SUFFIXES = {".md", ".ts", ".tsx", ".json", ".py"}


def slug(heading: str) -> str:
    """GitHub anchor rules: lowercase, umlauts kept, spaces to '-', other punctuation dropped."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    found: set[str] = set()
    counts: dict[str, int] = {}
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        match = re.match(r"^#{1,6}\s+(.*?)\s*#*$", line)
        if in_fence or not match:
            continue
        base = slug(match.group(1))
        n = counts.get(base, 0)
        counts[base] = n + 1
        found.add(base if n == 0 else f"{base}-{n}")
    return found


def referenced_anchors() -> list[tuple[Path, str]]:
    roots = [
        REPO / "frontend" / "src",
        REPO / "docs",
        REPO / "packages" / "znuny-addon",
        REPO / "backend" / "src",
    ]
    refs: list[tuple[Path, str]] = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in _SCAN_SUFFIXES or not path.is_file():
                continue
            if "node_modules" in path.parts or "superpowers" in path.parts:
                continue
            for anchor in _LINK.findall(path.read_text(encoding="utf-8", errors="ignore")):
                refs.append((path, unquote(anchor).rstrip(".,;")))
    return refs


def test_owned_headings_exist() -> None:
    lines = set(ITSM_MD.read_text(encoding="utf-8").splitlines())
    for heading in OWNED_HEADINGS:
        assert heading in lines, heading


def test_owned_sections_are_not_placeholders() -> None:
    text = ITSM_MD.read_text(encoding="utf-8")
    assert "\nFolgt.\n" not in text


def test_slug_rules() -> None:
    assert slug("Zurück zu Modus A") == "zurück-zu-modus-a"
    assert slug("Umstellung auf Tiqora (Ablauf)") == "umstellung-auf-tiqora-ablauf"
    assert slug("Was bleibt, was entfällt (je Znuny-Bildschirm)") == (
        "was-bleibt-was-entfällt-je-znuny-bildschirm"
    )


def test_every_itsm_anchor_resolves() -> None:
    known = anchors(ITSM_MD)
    refs = referenced_anchors()
    assert any(a == "zurück-zu-modus-a" for _, a in refs)  # the admin page links here
    assert any(a == "koexistenz-mit-znuny" for _, a in refs)  # parallel-operation.md points here
    missing = [(str(p.relative_to(REPO)), a) for p, a in refs if a not in known]
    assert not missing, missing
