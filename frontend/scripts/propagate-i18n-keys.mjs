#!/usr/bin/env node
/**
 * Copy keys that exist in en.json into every other locale file, using the
 * English string as the placeholder for anything not translated yet.
 *
 * Why this exists: adding a feature means adding keys to en.json + de.json,
 * and `check-i18n-keys.mjs` then fails for the other 47 locales. There is no
 * way to translate them all in the same change, and an English placeholder is
 * the accepted stand-in.
 *
 * What it deliberately does NOT do:
 *   - overwrite a key a locale already has (translations are never touched),
 *   - delete keys a locale has but en.json does not (that is a separate,
 *     riskier decision -- the report at the end just names them),
 *   - create a new locale file (that is `scaffold-locale.mjs`, which
 *     copyFileSync's en.json over the target and would wipe a real locale).
 *
 * Usage:
 *   node scripts/propagate-i18n-keys.mjs           # write
 *   node scripts/propagate-i18n-keys.mjs --check   # report only, exit 1 if work needed
 */

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const localesDir = join(here, "..", "src", "i18n", "locales");
const SOURCE = "en.json";
const checkOnly = process.argv.includes("--check");

const read = (file) =>
  JSON.parse(readFileSync(join(localesDir, file), "utf8"));

/**
 * Merge `source` into `target`, keeping every value `target` already has and
 * following `source`'s key order so diffs stay readable. Returns the merged
 * object plus the dotted paths that were added.
 */
function merge(source, target, prefix = "") {
  const out = {};
  const added = [];
  for (const [key, value] of Object.entries(source)) {
    const path = prefix ? `${prefix}.${key}` : key;
    const existing = target?.[key];
    const bothObjects =
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      existing !== null &&
      typeof existing === "object" &&
      !Array.isArray(existing);

    if (bothObjects) {
      const nested = merge(value, existing, path);
      out[key] = nested.merged;
      added.push(...nested.added);
    } else if (existing === undefined) {
      out[key] = value;
      added.push(path);
    } else {
      out[key] = existing;
    }
  }
  // Keys the locale has that en.json does not: keep them, report separately.
  for (const [key, value] of Object.entries(target ?? {})) {
    if (!(key in out)) out[key] = value;
  }
  return { merged: out, added };
}

function extraPaths(source, target, prefix = "") {
  const out = [];
  for (const [key, value] of Object.entries(target ?? {})) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (!(key in source)) {
      out.push(path);
    } else if (
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value)
    ) {
      out.push(...extraPaths(source[key] ?? {}, value, path));
    }
  }
  return out;
}

const source = read(SOURCE);
const files = readdirSync(localesDir)
  .filter((f) => f.endsWith(".json") && f !== SOURCE)
  .sort();

let touched = 0;
let totalAdded = 0;
const extras = [];

for (const file of files) {
  const target = read(file);
  const { merged, added } = merge(source, target);
  const extra = extraPaths(source, target);
  if (extra.length) extras.push(`${file}: ${extra.join(", ")}`);
  if (added.length === 0) continue;

  touched += 1;
  totalAdded += added.length;
  const preview = added.slice(0, 4).join(", ");
  const more = added.length > 4 ? ` (+${added.length - 4} more)` : "";
  console.log(`${file}: +${added.length} — ${preview}${more}`);
  if (!checkOnly) {
    writeFileSync(
      join(localesDir, file),
      `${JSON.stringify(merged, null, 2)}\n`,
      "utf8",
    );
  }
}

if (extras.length) {
  console.log("\nKeys present in a locale but not in en.json (left alone):");
  for (const line of extras) console.log(`  ${line}`);
}

if (touched === 0) {
  console.log("All locales already carry every en.json key.");
  process.exit(0);
}

console.log(
  `\n${checkOnly ? "Would add" : "Added"} ${totalAdded} key(s) across ${touched} locale(s).`,
);
process.exit(checkOnly ? 1 : 0);
