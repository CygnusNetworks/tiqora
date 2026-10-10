#!/usr/bin/env node
/**
 * After `vite build`: check the plugin runtime shims in dist/plugin-runtime/.
 * For every module in packages/plugin-sdk/src/shared.ts (PLUGIN_SHARED_MODULES):
 *   - dist/plugin-runtime/<file>.js exists,
 *   - every relative import in it (and in the chunks it pulls in) resolves to
 *     an existing file under dist/,
 *   - its export list contains the module's marker name.
 * Usage: node scripts/check-plugin-runtime.mjs   (exit 1 on any problem)
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const dist = resolve(__dirname, "../dist");
const sharedTs = resolve(__dirname, "../../packages/plugin-sdk/src/shared.ts");

/** An export each shim must have — proof it re-exports the right module. */
const MARKERS = {
  react: "useState",
  "react-jsx-runtime": "jsx",
  "react-dom": "createPortal",
  "react-query": "useQuery",
  "react-router": "Link",
  "react-i18next": "useTranslation",
  sdk: "definePlugin",
};

// shared.ts is TypeScript; read the file names as text instead of importing it
// (Node only strips types from version 22.18 on).
const files = [...readFileSync(sharedTs, "utf8").matchAll(/\bfile:\s*"([^"]+)"/g)].map((m) => m[1]);

const problems = [];
if (files.length === 0) problems.push(`no PLUGIN_SHARED_MODULES entries found in ${sharedTs}`);

/** Relative specifiers of static and dynamic imports in a built ES module. */
function relativeImports(code) {
  const re = /(?:\bfrom\s*|\bimport\s*\(?\s*)["'](\.{1,2}\/[^"']+)["']/g;
  return [...new Set([...code.matchAll(re)].map((m) => m[1]))];
}

/** Exported names of a built ES module (`export{a as b,c}` / `export const x`). */
function exportNames(code) {
  const names = new Set();
  for (const block of code.matchAll(/\bexport\s*\{([^}]*)\}/g)) {
    for (const part of block[1].split(",")) {
      const item = part.trim();
      if (!item) continue;
      const asMatch = item.match(/\bas\s+(\S+)$/);
      names.add((asMatch ? asMatch[1] : item).replace(/^["']|["']$/g, ""));
    }
  }
  for (const decl of code.matchAll(/\bexport\s+(?:const|let|var|function\*?|class)\s+([A-Za-z_$][\w$]*)/g)) {
    names.add(decl[1]);
  }
  return names;
}

/** Follow relative imports from `start`; report each one that does not resolve. */
function checkImports(start) {
  const seen = new Set();
  const queue = [start];
  while (queue.length > 0) {
    const file = queue.shift();
    if (seen.has(file)) continue;
    seen.add(file);
    for (const spec of relativeImports(readFileSync(file, "utf8"))) {
      const target = resolve(dirname(file), spec);
      const inDist = target === dist || target.startsWith(dist + sep);
      if (!inDist || !existsSync(target)) {
        problems.push(`${relative(dist, file)}: import "${spec}" does not resolve to a file in dist/`);
        continue;
      }
      if (target.endsWith(".js")) queue.push(target);
    }
  }
}

for (const file of files) {
  const marker = MARKERS[file];
  if (!marker) {
    problems.push(`no marker export known for shared module "${file}" — add it to MARKERS`);
    continue;
  }
  const path = join(dist, "plugin-runtime", `${file}.js`);
  if (!existsSync(path)) {
    problems.push(`dist/plugin-runtime/${file}.js is missing`);
    continue;
  }
  checkImports(path);
  if (!exportNames(readFileSync(path, "utf8")).has(marker)) {
    problems.push(`dist/plugin-runtime/${file}.js does not export "${marker}"`);
  }
}

if (problems.length > 0) {
  console.error("Plugin runtime check failed:");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log(`Plugin runtime OK: ${files.length} shim modules in dist/plugin-runtime/`);
