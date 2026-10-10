#!/usr/bin/env node
/**
 * After `vite build`: check the plugin runtime shims in dist/plugin-runtime/.
 * For every module in packages/plugin-sdk/src/shared.ts (PLUGIN_SHARED_MODULES):
 *   - dist/plugin-runtime/<file>.js exists,
 *   - every relative import in it (and in the chunks it pulls in) resolves to
 *     an existing file under dist/,
 *   - its export list contains the module's marker name.
 * And across the whole build (via the source maps):
 *   - no source module is bundled into more than one chunk (a second copy of
 *     a module is a second instance: second React, second context, …),
 *   - src/plugins/instanceContext.ts (PluginInstanceContext) lives in exactly
 *     one chunk, which plugin-runtime/sdk.js imports; once the app uses it
 *     too (PluginBoundary), the app reaches that same chunk, it lies in
 *     assets/, and no app chunk imports a plugin-runtime/ shim. Otherwise
 *     every SDK hook in a plugin throws "nur innerhalb einer Erweiterung".
 * Usage: node scripts/check-plugin-runtime.mjs   (exit 1 on any problem)
 */
import { existsSync, readFileSync, readdirSync } from "node:fs";
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

// --- one copy of every module, one PluginInstanceContext ----------------

/** Every built .js file under dist/ (recursive). */
function builtScripts(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...builtScripts(full));
    else if (entry.name.endsWith(".js")) out.push(full);
  }
  return out;
}

/** Files reachable from `start` through relative static/dynamic imports (incl. `start`). */
function reachableFrom(start) {
  const seen = new Set();
  const queue = [start];
  while (queue.length > 0) {
    const file = queue.shift();
    if (seen.has(file) || !existsSync(file)) continue;
    seen.add(file);
    for (const spec of relativeImports(readFileSync(file, "utf8"))) {
      const target = resolve(dirname(file), spec);
      if (target.endsWith(".js")) queue.push(target);
    }
  }
  return seen;
}

const frontendRoot = resolve(__dirname, "..");
const instanceContextSrc = resolve(frontendRoot, "src/plugins/instanceContext.ts");
let instanceContextChunks = [];

if (existsSync(dist)) {
  /** absolute source path -> chunks (dist-relative) that bundle it */
  const owners = new Map();
  let maps = 0;
  for (const chunk of builtScripts(dist)) {
    const mapFile = `${chunk}.map`;
    if (!existsSync(mapFile)) continue;
    maps += 1;
    const map = JSON.parse(readFileSync(mapFile, "utf8"));
    const root = resolve(dirname(mapFile), map.sourceRoot || "");
    for (const source of map.sources || []) {
      if (!source || source.startsWith("\0")) continue; // virtual helper modules
      const abs = resolve(root, source);
      if (!owners.has(abs)) owners.set(abs, new Set());
      owners.get(abs).add(relative(dist, chunk));
    }
  }
  if (maps === 0) {
    problems.push("no source maps in dist/ — the duplicate-module check needs build.sourcemap = true");
  }
  for (const [source, chunks] of owners) {
    if (chunks.size > 1) {
      problems.push(`${relative(frontendRoot, source)} is bundled into ${chunks.size} chunks: ${[...chunks].sort().join(", ")}`);
    }
  }

  instanceContextChunks = [...(owners.get(instanceContextSrc) || [])];
  const sdkShim = join(dist, "plugin-runtime", "sdk.js");
  if (instanceContextChunks.length !== 1) {
    problems.push(
      `src/plugins/instanceContext.ts must be in exactly one chunk, found ${instanceContextChunks.length}` +
        (instanceContextChunks.length ? `: ${instanceContextChunks.join(", ")}` : ""),
    );
  } else if (existsSync(sdkShim)) {
    const contextChunk = join(dist, instanceContextChunks[0]);
    if (!reachableFrom(sdkShim).has(contextChunk)) {
      problems.push(`plugin-runtime/sdk.js does not import ${instanceContextChunks[0]} (PluginInstanceContext)`);
    }
    const html = existsSync(join(dist, "index.html")) ? readFileSync(join(dist, "index.html"), "utf8") : "";
    const appEntries = [...html.matchAll(/<script[^>]*type="module"[^>]*src="[^"]*?(assets\/[^"]+\.js)"/g)].map((m) =>
      join(dist, m[1]),
    );
    if (appEntries.length === 0) problems.push("no module entry script found in dist/index.html");
    const app = new Set(appEntries.flatMap((entry) => [...reachableFrom(entry)]));
    for (const file of app) {
      if (relative(dist, file).startsWith(`plugin-runtime${sep}`)) {
        problems.push(`the app imports the plugin runtime shim ${relative(dist, file)}`);
      }
    }
    if (app.has(contextChunk)) {
      if (!instanceContextChunks[0].startsWith(`assets${sep}`)) {
        problems.push(`PluginInstanceContext is shared from ${instanceContextChunks[0]}, expected an assets/ chunk`);
      }
      console.log(`PluginInstanceContext: one instance in ${instanceContextChunks[0]}, used by the app and plugin-runtime/sdk.js`);
    } else {
      console.log(
        `PluginInstanceContext: one instance in ${instanceContextChunks[0]} (the app does not render plugin slots yet)`,
      );
    }
  }
}

if (problems.length > 0) {
  console.error("Plugin runtime check failed:");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log(`Plugin runtime OK: ${files.length} shim modules in dist/plugin-runtime/`);
