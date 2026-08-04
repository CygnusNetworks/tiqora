#!/usr/bin/env python3
"""Fill missing en.json leaf keys into every other locale via Google MT.

Preserves existing translations; only writes paths that are absent.
Never overwrites en.json or de.json (hand-maintained).

Usage (from frontend/):
  uv run --with deep-translator python scripts/fill_missing_i18n.py
  uv run --with deep-translator python scripts/fill_missing_i18n.py --only fr,es,it
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

# Reuse language map from the full translator.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from translate_locales import (  # noqa: E402
    CACHE_PATH,
    LOCALES_DIR,
    NEVER_OVERWRITE,
    PRIORITY,
    PROTECT,
    SKIP_EXACT,
    ZNUNY_REST,
    cache_key,
    leaf_paths,
    load_cache,
    make_translator,
    pack_batches,
    protect,
    save_cache,
    set_path,
    unprotect,
)

SEP = "\n⟨⟩\n"


def get_path(root: dict, path: str) -> object | None:
    cur: object = root
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Comma-separated locale codes")
    ap.add_argument("--sleep", type=float, default=0.12)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    en_leaves = leaf_paths(en)
    en_map = dict(en_leaves)

    all_map = {**PRIORITY, **ZNUNY_REST}
    # Also pick up any locale files not in the maps (copy as en fallback later).
    locale_files = sorted(
        p.stem for p in LOCALES_DIR.glob("*.json") if p.stem not in NEVER_OVERWRITE
    )
    if args.only:
        wanted = {c.strip() for c in args.only.split(",") if c.strip()}
        locale_files = [c for c in locale_files if c in wanted]

    cache = load_cache()
    total_filled = 0

    for code in locale_files:
        path = LOCALES_DIR / f"{code}.json"
        if not path.exists():
            print(f"skip {code}: no file")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [(p, en_map[p]) for p, _ in en_leaves if get_path(data, p) is None]
        if not missing:
            print(f"ok    {code}: complete")
            continue

        gt = all_map.get(code)
        print(f"\n=== {code} missing={len(missing)} gt={gt or 'en-fallback'} ===")

        if gt is None or gt == "en":
            # English variants / unknown: fill from en source.
            for p, text in missing:
                set_path(data, p, text)
            if not args.dry_run:
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            print(f"  filled {len(missing)} from en")
            total_filled += len(missing)
            continue

        try:
            translator, used = make_translator(gt)
        except Exception as e:  # noqa: BLE001
            print(f"  translator fail ({e}); filling from en")
            for p, text in missing:
                set_path(data, p, text)
            if not args.dry_run:
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            total_filled += len(missing)
            continue

        # Dedupe texts for MT.
        unique: dict[str, list[tuple[str, list[str]]]] = {}
        identity: list[tuple[str, str]] = []
        for p, text in missing:
            if not text.strip() or text in SKIP_EXACT:
                identity.append((p, text))
                continue
            protected, held = protect(text)
            unique.setdefault(protected, []).append((p, held))

        need = [t for t in unique if cache_key(used, t) not in cache]
        print(f"  unique={len(unique)} need_mt={len(need)} identity={len(identity)}")

        for bi, batch in enumerate(pack_batches(need)):
            blob = SEP.join(batch)
            try:
                result = translator.translate(blob)
                parts = result.split(SEP) if isinstance(result, str) else []
            except Exception as e:  # noqa: BLE001
                print(f"  pack fail ({e}); singles")
                result, parts = None, []

            if not isinstance(result, str) or len(parts) != len(batch):
                for t in batch:
                    try:
                        cache[cache_key(used, t)] = translator.translate(t)
                    except Exception:  # noqa: BLE001
                        cache[cache_key(used, t)] = t
                    time.sleep(0.05)
            else:
                for src, dst in zip(batch, parts, strict=True):
                    cache[cache_key(used, src)] = dst.strip() or src

            if not args.dry_run:
                save_cache(cache)
            print(f"  batch {bi + 1} ({len(batch)})")
            time.sleep(args.sleep)

        for p, text in identity:
            set_path(data, p, text)
        for protected, occ in unique.items():
            translated = cache.get(cache_key(used, protected), protected)
            for p, held in occ:
                set_path(data, p, unprotect(translated, held))

        if not args.dry_run:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"  wrote {path.name}")
        total_filled += len(missing)

    if not args.dry_run:
        save_cache(cache)
    print(f"\nDone. filled≈{total_filled} leaf slots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
