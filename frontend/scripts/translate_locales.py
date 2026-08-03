#!/usr/bin/env python3
"""Machine-translate high-visibility UI namespaces into priority languages.

Admin UI strings stay English (source) so we can ship ~15 languages without
waiting on a full 2k-key MT pass. Agent/portal namespaces are fully MT'd.

Usage (from frontend/):
  uv run --with deep-translator python scripts/translate_locales.py --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent.parent / "src" / "i18n" / "locales"
CACHE_PATH = Path(__file__).resolve().parent / ".mt-cache.json"

PRIORITY: dict[str, str] = {
    "fr": "fr",
    "es": "es",
    "it": "it",
    "nl": "nl",
    "pl": "pl",
    "pt_BR": "pt",
    "ru": "ru",
    "zh_CN": "zh-CN",
    "ja": "ja",
    "tr": "tr",
    "cs": "cs",
    "hu": "hu",
    "sv": "sv",
}

# Fully machine-translated namespaces (agent + portal + shared chrome).
MT_NAMESPACES = frozenset(
    {
        "app",
        "notifications",
        "onlineAgents",
        "settings",
        "nav",
        "sidebar",
        "common",
        "otp",
        "auth",
        "security",
        "dashboard",
        "queue",
        "ticket",
        "search",
        "account",
        "newTicket",
        "connection",
        "shortcuts",
        "agent",
        "portal",
        "kb",
        "stats",
        "calendar",
        "process",
        "agentTemplates",
    }
)

PROTECT = re.compile(r"(\{\{[^}]+\}\}|</?[a-zA-Z][^>]*>|%[sd]|Ticket#\{|\{0\}|\{1\})")
SKIP_EXACT = frozenset(
    {
        "Tiqora",
        "OTP",
        "2FA",
        "API",
        "MCP",
        "KB",
        "EN",
        "DE",
        "ID",
        "PDF",
        "CSV",
        "JSON",
        "HTML",
        "SMTP",
        "IMAP",
        "LDAP",
        "SSO",
        "OIDC",
        "AI",
        "SSE",
        "TOTP",
        "QR",
    }
)
SEP = "\n⟨⟩\n"


def leaf_paths(obj: object, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            out.extend(leaf_paths(v, path))
    else:
        out.append((prefix, str(obj)))
    return out


def set_path(root: dict, path: str, value: str) -> None:
    parts = path.split(".")
    cur: dict = root
    for p in parts[:-1]:
        nxt = cur.setdefault(p, {})
        assert isinstance(nxt, dict)
        cur = nxt
    cur[parts[-1]] = value


def protect(s: str) -> tuple[str, list[str]]:
    held: list[str] = []

    def repl(m: re.Match[str]) -> str:
        held.append(m.group(0))
        return f"⟦{len(held) - 1}⟧"

    return PROTECT.sub(repl, s), held


def unprotect(s: str, held: list[str]) -> str:
    def repl(m: re.Match[str]) -> str:
        i = int(m.group(1))
        return held[i] if 0 <= i < len(held) else m.group(0)

    return re.sub(r"⟦(\d+)⟧", repl, s)


def load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False) + "\n", encoding="utf-8")


def cache_key(target: str, text: str) -> str:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return f"{target}:{h}"


def pack_batches(texts: list[str], max_chars: int = 4000) -> list[list[str]]:
    batches: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for t in texts:
        add = len(t) + len(SEP)
        if cur and size + add > max_chars:
            batches.append(cur)
            cur = []
            size = 0
        cur.append(t)
        size += add
    if cur:
        batches.append(cur)
    return batches


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    targets = PRIORITY
    if args.only:
        wanted = {c.strip() for c in args.only.split(",") if c.strip()}
        targets = {k: v for k, v in PRIORITY.items() if k in wanted}

    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    all_leaves = leaf_paths(en)
    mt_leaves = [(p, t) for p, t in all_leaves if p.split(".", 1)[0] in MT_NAMESPACES]
    print(f"total keys={len(all_leaves)} mt_keys={len(mt_leaves)}")

    from deep_translator import GoogleTranslator  # type: ignore[import-untyped]

    cache = load_cache()
    for code, gt_code in targets.items():
        out_path = LOCALES_DIR / f"{code}.json"
        if out_path.exists() and not args.force:
            print(f"skip {code}")
            continue

        print(f"\n=== {code} → {gt_code} ===")
        translator = GoogleTranslator(source="en", target=gt_code)

        unique: dict[str, list[tuple[str, list[str]]]] = {}
        for path, text in mt_leaves:
            if not text.strip() or text in SKIP_EXACT:
                continue
            protected, held = protect(text)
            unique.setdefault(protected, []).append((path, held))

        need = [t for t in unique if cache_key(gt_code, t) not in cache]
        print(f"  unique={len(unique)} need={len(need)}")

        for bi, batch in enumerate(pack_batches(need)):
            blob = SEP.join(batch)
            try:
                result = translator.translate(blob)
            except Exception as e:  # noqa: BLE001
                print(f"  pack fail ({e}); single")
                for t in batch:
                    try:
                        cache[cache_key(gt_code, t)] = translator.translate(t)
                    except Exception:  # noqa: BLE001
                        cache[cache_key(gt_code, t)] = t
                    time.sleep(0.05)
                save_cache(cache)
                continue

            parts = result.split(SEP) if isinstance(result, str) else []
            if len(parts) != len(batch):
                # separator mangled — fall back per string
                print(f"  split mismatch {len(parts)}!={len(batch)}; singles")
                for t in batch:
                    try:
                        cache[cache_key(gt_code, t)] = translator.translate(t)
                    except Exception:  # noqa: BLE001
                        cache[cache_key(gt_code, t)] = t
                    time.sleep(0.05)
            else:
                for src, dst in zip(batch, parts, strict=True):
                    cache[cache_key(gt_code, src)] = dst.strip() or src

            save_cache(cache)
            print(f"  batch {bi + 1} ({len(batch)} strings)")
            time.sleep(args.sleep)

        out = json.loads(json.dumps(en))  # admin etc. stay English
        for path, text in mt_leaves:
            if not text.strip() or text in SKIP_EXACT:
                continue
            protected, held = protect(text)
            translated = cache.get(cache_key(gt_code, protected), protected)
            set_path(out, path, unprotect(translated, held))

        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  wrote {out_path.name}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
