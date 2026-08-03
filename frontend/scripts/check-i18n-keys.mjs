#!/usr/bin/env node
/**
 * Fail when a locale JSON is missing keys that exist in en.json (or has extras).
 * Usage: node scripts/check-i18n-keys.mjs
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const localesDir = join(__dirname, "../src/i18n/locales");

function leafPaths(obj, prefix = "") {
  const out = [];
  if (obj !== null && typeof obj === "object" && !Array.isArray(obj)) {
    for (const [k, v] of Object.entries(obj)) {
      const path = prefix ? `${prefix}.${k}` : k;
      out.push(...leafPaths(v, path));
    }
  } else {
    out.push(prefix);
  }
  return out;
}

const files = readdirSync(localesDir).filter((f) => f.endsWith(".json"));
if (!files.includes("en.json")) {
  console.error("en.json missing from locales/");
  process.exit(1);
}

const en = JSON.parse(readFileSync(join(localesDir, "en.json"), "utf8"));
const enKeys = new Set(leafPaths(en));
let failed = false;

for (const file of files) {
  if (file === "en.json") continue;
  const data = JSON.parse(readFileSync(join(localesDir, file), "utf8"));
  const keys = new Set(leafPaths(data));
  const missing = [...enKeys].filter((k) => !keys.has(k)).sort();
  const extra = [...keys].filter((k) => !enKeys.has(k)).sort();
  if (missing.length || extra.length) {
    failed = true;
    console.error(`\n${file}:`);
    if (missing.length) {
      console.error(`  missing (${missing.length}):`);
      for (const k of missing.slice(0, 40)) console.error(`    - ${k}`);
      if (missing.length > 40) console.error(`    … +${missing.length - 40} more`);
    }
    if (extra.length) {
      console.error(`  extra (${extra.length}):`);
      for (const k of extra.slice(0, 40)) console.error(`    + ${k}`);
      if (extra.length > 40) console.error(`    … +${extra.length - 40} more`);
    }
  } else {
    console.log(`${file}: OK (${keys.size} keys)`);
  }
}

if (failed) {
  console.error("\ni18n key parity check failed.");
  process.exit(1);
}
console.log(`\nAll ${files.length - 1} locale(s) match en.json (${enKeys.size} keys).`);
