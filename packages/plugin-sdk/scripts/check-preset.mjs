#!/usr/bin/env node
/**
 * After `vite build -c test-plugin/vite.config.ts`: check that the preset
 * produced a bundle that uses the page's shared modules instead of its own
 * copies. Every import specifier must start with /plugin-runtime/ (a runtime
 * shim) or ./ (a file of the plugin itself), and no React internals may be
 * bundled. Exit 1 with a message otherwise.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const bundle = resolve(__dirname, "../test-plugin/dist/index.js");

if (!existsSync(bundle)) {
  console.error(`check-preset: ${bundle} is missing — did the test-plugin build run?`);
  process.exit(1);
}
const code = readFileSync(bundle, "utf8");

const specifiers = [
  ...code.matchAll(/\bfrom\s*["']([^"']+)["']/g),
  ...code.matchAll(/\bimport\s*\(\s*["']([^"']+)["']\s*\)/g),
  ...code.matchAll(/\bimport\s*["']([^"']+)["']/g),
].map((m) => m[1]);

const problems = [];
if (specifiers.length === 0) problems.push("no imports found — the shared modules were bundled instead of imported");
for (const spec of new Set(specifiers)) {
  if (!spec.startsWith("/plugin-runtime/") && !spec.startsWith("./")) {
    problems.push(`import "${spec}" is neither a /plugin-runtime/ shim nor a local ./ file`);
  }
}
for (const needle of ["react.production", "__SECRET_INTERNALS", "__CLIENT_INTERNALS"]) {
  if (code.includes(needle)) problems.push(`bundle contains "${needle}" — React was bundled into the plugin`);
}

if (problems.length > 0) {
  console.error("check-preset failed for test-plugin/dist/index.js:");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log(`check-preset OK: ${new Set(specifiers).size} imports, all from /plugin-runtime/ or ./`);
