#!/usr/bin/env node
// Render site/og-image.html to docs/images/og-image.png (1280x640 social preview).
// The PNG lives next to the screenshots: the pre-commit hook only allows images there.
// Usage from the repo root: node site/render-og-image.mjs
// Uses the Playwright install of the frontend workspace.
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, "../frontend/package.json"));
const { chromium } = require("@playwright/test");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 640 } });
await page.goto(pathToFileURL(join(here, "og-image.html")).href, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: join(here, "../docs/images/og-image.png") });
await browser.close();
console.log("wrote docs/images/og-image.png");
