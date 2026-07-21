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
async function shot(page: Page, name: string) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
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
    await page.goto(route);
    await shot(page, name);
  }
  // User menu open (best-effort — never fail the run over it)
  try {
    await page.goto("/agent");
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await page.waitForTimeout(500);
    await page.locator('[data-testid="account-menu-trigger"]:visible').first().click();
    await page.getByTestId("account-menu").waitFor({ state: "visible", timeout: 3000 });
    await page.waitForTimeout(400);
  } catch {
    /* ignore */
  }
  await page.screenshot({ path: `${OUT}/user-menu.png`, fullPage: false });
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
    await page.goto(route);
    await shot(page, name);
  }
});

test("portal + login + security screenshots", async ({ page }) => {
  await prep(page);
  await mockRich(page);
  await page.goto("/login");
  await shot(page, "login");
  await loginAsAgentRich(page);
  await page.goto("/agent/security");
  await shot(page, "agent-security");
  await page.goto("/portal");
  await shot(page, "portal");
});
