#!/usr/bin/env node
/**
 * Scaffold a new locale JSON from en.json (English source strings as placeholders).
 *
 * Usage:
 *   node scripts/scaffold-locale.mjs fr
 *   node scripts/scaffold-locale.mjs pt_BR --force
 *
 * After scaffolding:
 *   1. Mark the locale `translated: true` in src/i18n/locales.ts
 *   2. Add an eager import or localeLoaders entry in src/i18n/index.ts
 *   3. Translate all strings by hand (no machine-translation pipeline)
 *   4. Run: node scripts/check-i18n-keys.mjs
 */
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const localesDir = join(__dirname, "../src/i18n/locales");
const registryPath = join(__dirname, "../src/i18n/locales.ts");

const args = process.argv.slice(2).filter((a) => a !== "--force");
const force = process.argv.includes("--force");
const code = args[0];

if (!code || !/^[a-z]{2}(_[A-Z]{2})?$/.test(code)) {
  console.error("Usage: node scripts/scaffold-locale.mjs <code> [--force]");
  console.error("  code: Znuny-style language code, e.g. fr, pt_BR, zh_CN");
  process.exit(1);
}

const enPath = join(localesDir, "en.json");
const outPath = join(localesDir, `${code}.json`);

if (!existsSync(enPath)) {
  console.error("en.json not found");
  process.exit(1);
}

if (existsSync(outPath) && !force) {
  console.error(`${outPath} already exists (pass --force to overwrite)`);
  process.exit(1);
}

copyFileSync(enPath, outPath);
console.log(`Wrote ${outPath} (copy of en.json — translate before shipping)`);

// Hint if the code is not yet in the registry
const registry = readFileSync(registryPath, "utf8");
if (!registry.includes(`code: "${code}"`)) {
  console.warn(
    `\nNote: "${code}" is not listed in src/i18n/locales.ts — add a LocaleDef entry.`,
  );
} else if (registry.includes(`code: "${code}",`) && registry.includes("translated: false")) {
  console.warn(
    `\nNext: set translated: true for "${code}" in locales.ts and register the JSON in i18n/index.ts`,
  );
}

// Touch package script reminder
console.log("\nVerify key parity:  node scripts/check-i18n-keys.mjs");
