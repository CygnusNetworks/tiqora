#!/usr/bin/env python3
"""Fully translate en.json into every target language (all namespaces).

Uses deep-translator (Google free endpoint) with packed batches + on-disk cache.
Never overwrites en.json or de.json (hand-maintained).

Usage (from frontend/):
  uv run --with deep-translator python scripts/translate_locales.py --force
  uv run --with deep-translator python scripts/translate_locales.py --only fr,es --force
  uv run --with deep-translator python scripts/translate_locales.py --all-znuny --force
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

# Tiqora code → Google Translate target
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

# Remaining Znuny PO codes (beyond priority + en/de)
ZNUNY_REST: dict[str, str] = {
    "ar_SA": "ar",
    "bg": "bg",
    "ca": "ca",
    "da": "da",
    "el": "el",
    "en_CA": "en",  # will mostly stay English; still run for regional if any
    "en_GB": "en",
    "es_CO": "es",
    "es_MX": "es",
    "et": "et",
    "fa": "fa",
    "fi": "fi",
    "fr_CA": "fr",
    "gl": "gl",
    "he": "iw",  # Google uses iw for Hebrew historically; try he if fails
    "hi": "hi",
    "hr": "hr",
    "id": "id",
    "ko": "ko",
    "lt": "lt",
    "lv": "lv",
    "mk": "mk",
    "ms": "ms",
    "nb_NO": "no",
    "pt": "pt",
    "ro": "ro",
    "sk_SK": "sk",
    "sl": "sl",
    "sr": "sr",
    "sw": "sw",
    "th_TH": "th",
    "uk": "uk",
    "vi_VN": "vi",
    "zh_TW": "zh-TW",
}

NEVER_OVERWRITE = frozenset({"en", "de"})

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
        "Beta",
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
        out.append((prefix, str(obj) if not isinstance(obj, str) else obj))
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
            cur, size = [], 0
        cur.append(t)
        size += add
    if cur:
        batches.append(cur)
    return batches


def make_translator(gt_code: str):
    from deep_translator import GoogleTranslator  # type: ignore[import-untyped]

    # Hebrew fallback: Google may want 'iw' or 'he'
    codes = [gt_code]
    if gt_code in ("iw", "he"):
        codes = ["he", "iw"]
    last_err: Exception | None = None
    for c in codes:
        try:
            t = GoogleTranslator(source="en", target=c)
            # smoke
            t.translate("Hello")
            return t, c
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise RuntimeError(f"Cannot init translator for {gt_code}: {last_err}")


def translate_locale(
    code: str,
    gt_code: str,
    en: dict,
    leaves: list[tuple[str, str]],
    cache: dict[str, str],
    *,
    force: bool,
    sleep: float,
) -> None:
    out_path = LOCALES_DIR / f"{code}.json"
    if out_path.exists() and not force:
        print(f"skip {code} (exists)")
        return
    if code in NEVER_OVERWRITE:
        print(f"skip {code} (hand-maintained)")
        return

    print(f"\n=== {code} → {gt_code} ===")
    # English variants: just copy en (still full files for key parity)
    if gt_code == "en":
        out_path.write_text(json.dumps(en, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  wrote {out_path.name} (en copy)")
        return

    translator, used = make_translator(gt_code)
    if used != gt_code:
        print(f"  using gt code {used}")

    unique: dict[str, list[tuple[str, list[str]]]] = {}
    identity: list[tuple[str, str]] = []
    for path, text in leaves:
        if not text.strip() or text in SKIP_EXACT:
            identity.append((path, text))
            continue
        protected, held = protect(text)
        unique.setdefault(protected, []).append((path, held))

    need = [t for t in unique if cache_key(used, t) not in cache]
    print(f"  unique={len(unique)} need={len(need)} identity={len(identity)}")

    for bi, batch in enumerate(pack_batches(need)):
        blob = SEP.join(batch)
        try:
            result = translator.translate(blob)
            parts = result.split(SEP) if isinstance(result, str) else []
        except Exception as e:  # noqa: BLE001
            print(f"  pack fail ({e}); singles")
            parts = []
            result = None

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

        save_cache(cache)
        print(f"  batch {bi + 1}/{len(pack_batches(need))} ({len(batch)})")
        time.sleep(sleep)

    out = json.loads(json.dumps(en))
    for path, text in identity:
        set_path(out, path, text)
    for protected, occ in unique.items():
        translated = cache.get(cache_key(used, protected), protected)
        for path, held in occ:
            set_path(out, path, unprotect(translated, held))

    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {out_path.name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Comma-separated codes")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--all-znuny", action="store_true", help="Also remaining Znuny codes")
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()

    targets: dict[str, str] = dict(PRIORITY)
    if args.all_znuny:
        targets.update(ZNUNY_REST)
    if args.only:
        all_map = {**PRIORITY, **ZNUNY_REST}
        wanted = {c.strip() for c in args.only.split(",") if c.strip()}
        targets = {k: all_map[k] for k in wanted if k in all_map}
        missing = wanted - set(targets)
        if missing:
            print(f"unknown: {sorted(missing)}", file=sys.stderr)
            return 1

    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    leaves = leaf_paths(en)
    print(f"source keys={len(leaves)} targets={len(targets)}")

    cache = load_cache()
    for code, gt in targets.items():
        try:
            translate_locale(code, gt, en, leaves, cache, force=args.force, sleep=args.sleep)
        except Exception as e:  # noqa: BLE001
            print(f"FAILED {code}: {e}", file=sys.stderr)
            save_cache(cache)
            continue

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
