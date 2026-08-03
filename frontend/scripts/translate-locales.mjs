#!/usr/bin/env node
/**
 * Machine-translate en.json into priority locale files via deep-translator
 * (Google free endpoint). Run with:
 *
 *   uv run --with deep-translator python scripts/translate_locales.py
 *
 * This file is a thin Node launcher — the real work is in translate_locales.py
 * so we can reuse the project's Python tooling and rate-limit politely.
 */
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dir = dirname(fileURLToPath(import.meta.url));
const py = join(dir, "translate_locales.py");
const r = spawnSync(
  "uv",
  ["run", "--with", "deep-translator", "python", py, ...process.argv.slice(2)],
  { stdio: "inherit", cwd: join(dir, "..") },
);
process.exit(r.status ?? 1);
