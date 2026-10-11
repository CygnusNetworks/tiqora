#!/usr/bin/env node
/**
 * Size budget of the widget build (R-BUDGET), the last step of `pnpm build` — so CI and
 * the Docker frontend-build stage both fail on an overrun.
 *
 * - dist/loader.js (loaded on every host-page view): gzip <= 5 120 B.
 * - Frame start: gzip(frame entry JS + every statically imported chunk + CSS, taken from
 *   dist/frame.html AND the Vite manifest) + gzip(largest per-language chunk
 *   assets/webchat-locale-*.js, one of which is fetched on every frame start)
 *   <= 40 960 B.
 *
 * Every file is gzipped on its own (that is how the browser fetches them).
 */
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const LOADER_BUDGET = 5120;
const FRAME_BUDGET = 40960;
const BASE = "/chat/v1/";
const LOCALE_PREFIX = "webchat-locale-";

const dist = join(dirname(fileURLToPath(import.meta.url)), "..", "dist");
const errors = [];

const gz = (rel) => gzipSync(readFileSync(join(dist, rel)), { level: 9 }).length;
const fmt = (n) => `${n.toLocaleString("en-US")} B`;

function fail(message) {
  console.error(`check-size: ${message}`);
  process.exit(1);
}

for (const required of ["loader.js", "frame.html", ".vite/manifest.json"]) {
  if (!existsSync(join(dist, required))) fail(`dist/${required} is missing — run the build first.`);
}

// --- loader -------------------------------------------------------------------------
const loaderSize = gz("loader.js");
console.log(`dist/loader.js  ${fmt(loaderSize)} gzip  (budget ${fmt(LOADER_BUDGET)})`);
if (loaderSize > LOADER_BUDGET) {
  errors.push(`dist/loader.js is ${fmt(loaderSize)} gzip, budget ${fmt(LOADER_BUDGET)}`);
}

// --- frame: files referenced by frame.html ---------------------------------------------
const html = readFileSync(join(dist, "frame.html"), "utf8");
if (/<script(?![^>]*\bsrc=)[^>]*>/i.test(html) || /<style[\s>]/i.test(html)) {
  errors.push("dist/frame.html contains an inline <script> or <style> (the frame CSP forbids it)");
}
const startFiles = new Set();
for (const m of html.matchAll(/\b(?:src|href)="([^"]+)"/g)) {
  const url = m[1];
  if (!url.startsWith(BASE)) {
    errors.push(`dist/frame.html references ${url} outside ${BASE}`);
    continue;
  }
  startFiles.add(url.slice(BASE.length));
}

// --- frame: manifest closure (entry + static imports + their CSS) ----------------------
const manifest = JSON.parse(readFileSync(join(dist, ".vite", "manifest.json"), "utf8"));
const entryKey = Object.keys(manifest).find(
  (k) => manifest[k].isEntry && manifest[k].src?.endsWith("frame.html"),
);
if (!entryKey) fail("no frame.html entry in dist/.vite/manifest.json");
const seen = new Set();
const walk = (key) => {
  if (seen.has(key) || !manifest[key]) return;
  seen.add(key);
  const chunk = manifest[key];
  startFiles.add(chunk.file);
  for (const css of chunk.css ?? []) startFiles.add(css);
  for (const imp of chunk.imports ?? []) walk(imp);
};
walk(entryKey);

for (const file of startFiles) {
  if (file.endsWith(".html")) startFiles.delete(file);
}
if (![...startFiles].some((f) => f.endsWith(".js"))) {
  errors.push("frame start loads no JavaScript — the frame build looks broken");
}

let frameTotal = 0;
for (const file of [...startFiles].sort()) {
  if (!existsSync(join(dist, file))) {
    errors.push(`dist/${file} referenced by the frame but missing`);
    continue;
  }
  const size = gz(file);
  frameTotal += size;
  console.log(`dist/${file}  ${fmt(size)} gzip`);
}

// --- frame: the largest per-language chunk ----------------------------------------------
const assets = existsSync(join(dist, "assets")) ? readdirSync(join(dist, "assets")) : [];
const locales = assets
  .filter((f) => f.startsWith(LOCALE_PREFIX) && f.endsWith(".js"))
  .map((f) => ({ file: `assets/${f}`, size: gz(`assets/${f}`) }))
  .sort((a, b) => b.size - a.size);
if (locales.length === 0) {
  errors.push(`no dist/assets/${LOCALE_PREFIX}*.js chunks — the locale extraction looks broken`);
} else {
  const largest = locales[0];
  frameTotal += largest.size;
  console.log(
    `dist/${largest.file}  ${fmt(largest.size)} gzip  (largest of ${locales.length} locale chunks)`,
  );
}

console.log(`frame start total  ${fmt(frameTotal)} gzip  (budget ${fmt(FRAME_BUDGET)})`);
if (frameTotal > FRAME_BUDGET) {
  errors.push(
    `frame start (dist/frame.html: entry + static imports + CSS + largest locale chunk) is ` +
      `${fmt(frameTotal)} gzip, budget ${fmt(FRAME_BUDGET)}`,
  );
}

if (errors.length) {
  for (const e of errors) console.error(`check-size: ${e}`);
  process.exit(1);
}
console.log("check-size: OK");
