#!/usr/bin/env node
/**
 * List the strings of a locale that are still the English placeholder.
 *
 * A key counts as untranslated when its value equals en.json AND de.json has a
 * different value (German is complete, so a German string that equals the English
 * one is a word that stays the same, like "Status" or "PGP").
 *
 * Usage:
 *   node scripts/untranslated-keys.mjs           # summary for every locale
 *   node scripts/untranslated-keys.mjs fr        # keys + English text for fr
 *   node scripts/untranslated-keys.mjs fr --json # same, as a JSON object to translate
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const localesDir = join(__dirname, "../src/i18n/locales");
// English variants and the two hand-maintained source locales.
const SKIP = new Set(["en", "en_GB", "en_CA", "de"]);

function leaves(obj, prefix = "", out = new Map()) {
  if (obj !== null && typeof obj === "object" && !Array.isArray(obj)) {
    for (const [k, v] of Object.entries(obj)) leaves(v, prefix ? `${prefix}.${k}` : k, out);
  } else {
    out.set(prefix, obj);
  }
  return out;
}

const load = (code) => leaves(JSON.parse(readFileSync(join(localesDir, `${code}.json`), "utf8")));
const en = load("en");
const de = load("de");

function untranslated(code) {
  const loc = load(code);
  return [...en].filter(([k, v]) => loc.get(k) === v && de.get(k) !== v);
}

const [code, flag] = process.argv.slice(2);

if (!code) {
  const codes = readdirSync(localesDir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.slice(0, -5))
    .filter((c) => !SKIP.has(c));
  const rows = codes.map((c) => [c, untranslated(c).length]).sort((a, b) => a[1] - b[1]);
  for (const [c, n] of rows) console.log(`${c.padEnd(6)} ${String(n).padStart(5)} untranslated`);
  console.log(`\n${en.size} keys in en.json. Details: node scripts/untranslated-keys.mjs <code>`);
} else {
  if (SKIP.has(code)) {
    console.error(`${code} is a source/English locale; nothing to list.`);
    process.exit(1);
  }
  const rows = untranslated(code);
  if (flag === "--json") {
    console.log(JSON.stringify(Object.fromEntries(rows), null, 2));
  } else {
    for (const [k, v] of rows) console.log(`${k}\t${v}`);
    console.error(`\n${rows.length} untranslated string(s) in ${code}.json`);
  }
}
