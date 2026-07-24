import { test, type Page } from "@playwright/test";
import { mockRich, loginAsAgentRich } from "./fixtures/rich-mock";

/**
 * Screenshot generator for the README / GitHub project page. Not part of the
 * normal e2e run — gated behind SCREENSHOTS=1 so CI skips it. Uses the rich
 * self-contained mock (English data, generous volume) — no backend.
 *
 *   SCREENSHOTS=1 pnpm exec playwright test screenshots --project=chromium
 *
 * Writes PNGs to ../docs/images/.
 */

const OUT = "../docs/images";
const THEME = process.env.THEME === "dark" ? "dark" : "light";
const LANG = process.env.LANG_UI || "en";

test.skip(!process.env.SCREENSHOTS, "screenshot generator — set SCREENSHOTS=1 to run");
test.use({ viewport: { width: 1440, height: 900 } });
// Each test walks many routes (settle per shot), so the default 30s per-test
// timeout is too tight — give the generator room.
test.beforeEach(() => test.setTimeout(180_000));

async function prep(page: Page) {
  await page.addInitScript(
    ({ theme, lang }) => {
      try {
        localStorage.setItem("tiqora-theme", theme);
        localStorage.setItem("tiqora-lang", lang);
      } catch {
        /* ignore */
      }
    },
    { theme: THEME, lang: LANG },
  );
}

/**
 * Navigate + capture, best-effort. Uses `domcontentloaded` (not the default
 * `load`) so a route holding a long-lived request — SSE, a slow poll — can't
 * hang `goto` indefinitely, and swallows per-route errors so one bad page
 * never aborts the whole batch (which would leave later PNGs stale).
 */
async function shot(page: Page, route: string, name: string) {
  try {
    await page.goto(route, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
  } catch (err) {
    console.warn(`screenshot "${name}" (${route}) failed:`, err);
  }
}

test("agent screenshots", async ({ page }) => {
  await prep(page);
  await loginAsAgentRich(page);
  for (const [route, name] of [
    ["/agent", "agent-dashboard"],
    ["/agent/queues", "agent-queues"],
    ["/agent/tickets/100", "agent-ticket-zoom"],
    ["/agent/stats", "agent-stats"],
    ["/agent/calendar", "agent-calendar"],
    ["/agent/kb", "agent-kb"],
    ["/agent/search?q=server", "agent-search"],
  ] as const) {
    await shot(page, route, name);
  }
  // User menu open (best-effort — never fail the run over it)
  try {
    await page.goto("/agent", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await page.waitForTimeout(500);
    await page.locator('[data-testid="account-menu-trigger"]:visible').first().click();
    await page.getByTestId("account-menu").waitFor({ state: "visible", timeout: 3000 });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/user-menu.png`, fullPage: false });
  } catch {
    /* ignore */
  }
});

test("admin screenshots", async ({ page }) => {
  await prep(page);
  await loginAsAgentRich(page);
  for (const [route, name] of [
    ["/admin/queues", "admin-queues"],
    ["/admin/users", "admin-users"],
    ["/admin/dynamic-fields", "admin-dynamic-fields"],
    ["/admin/customer-users", "admin-customer-users"],
    ["/admin/groups", "admin-groups"],
    ["/admin/customer-user-groups", "admin-customer-user-groups"],
    ["/admin/role-groups", "admin-role-groups"],
    ["/admin/auth-config", "admin-2fa"],
    ["/admin/gdpr", "admin-gdpr"],
  ] as const) {
    await shot(page, route, name);
  }
});

test("portal + login + security screenshots", async ({ page }) => {
  await prep(page);
  await mockRich(page);
  await shot(page, "/login", "login");
  await loginAsAgentRich(page);
  await shot(page, "/agent/security", "agent-security");
  await shot(page, "/portal", "portal");
});
