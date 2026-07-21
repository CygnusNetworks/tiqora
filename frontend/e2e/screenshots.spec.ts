import { test, type Page } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";
import { mockAdminApi } from "./fixtures/mock-admin-api";
import { mockPortalApi, loginAsCustomer } from "./fixtures/mock-portal-api";

/**
 * Screenshot generator for the README / GitHub project page. Not part of the
 * normal e2e run — gated behind SCREENSHOTS=1 so CI skips it. Reuses the same
 * fully-mocked fixtures the e2e suite uses (no backend), so the shots show the
 * real UI rendered from representative data.
 *
 *   SCREENSHOTS=1 THEME=light pnpm exec playwright test screenshots --project=chromium
 *
 * Writes PNGs to ../docs/images/.
 */

const OUT = "../docs/images";
const THEME = process.env.THEME === "dark" ? "dark" : "light";

test.skip(!process.env.SCREENSHOTS, "screenshot generator — set SCREENSHOTS=1 to run");

test.use({ viewport: { width: 1440, height: 900 } });

async function prep(page: Page) {
  // Force a deterministic theme before the app boots.
  await page.addInitScript((theme) => {
    try {
      localStorage.setItem("tiqora-theme", theme as string);
      localStorage.setItem("tiqora-lang", "de");
    } catch {
      /* ignore */
    }
  }, THEME);
}

async function shot(page: Page, name: string) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  // Let fonts/charts/transitions settle.
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
}

test("agent screenshots", async ({ page }) => {
  await prep(page);
  await mockApi(page);
  await loginAsAgent(page);

  for (const [route, name] of [
    ["/agent", "agent-dashboard"],
    ["/agent/queues", "agent-queues"],
    ["/agent/tickets/100", "agent-ticket-zoom"],
    ["/agent/stats", "agent-stats"],
    ["/agent/calendar", "agent-calendar"],
    ["/agent/kb", "agent-kb"],
    ["/agent/search?q=server", "agent-search"],
  ] as const) {
    await page.goto(route);
    await shot(page, name);
  }
});

test("admin screenshots", async ({ page }) => {
  await prep(page);
  await mockAdminApi(page);
  await loginAsAgent(page);

  for (const [route, name] of [
    ["/admin/queues", "admin-queues"],
    ["/admin/users", "admin-users"],
    ["/admin/dynamic-fields", "admin-dynamic-fields"],
  ] as const) {
    await page.goto(route);
    await shot(page, name);
  }
});

test("portal + login screenshots", async ({ page }) => {
  await prep(page);
  await page.goto("/login");
  await shot(page, "login");

  await mockPortalApi(page);
  await loginAsCustomer(page);
  await page.goto("/portal");
  await shot(page, "portal");
});
