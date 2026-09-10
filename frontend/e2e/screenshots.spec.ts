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
// Dark shots land beside the light ones with a `-dark` suffix so the site can
// swap between the two (`THEME=dark SCREENSHOTS=1 …` to regenerate them).
const SUFFIX = THEME === "dark" ? "-dark" : "";
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
    await page.screenshot({ path: `${OUT}/${name}${SUFFIX}.png`, fullPage: false });
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
  // AI assist panel (summary + drafts) — a focused element shot of the panel
  // on the ticket zoom (best-effort).
  try {
    await page.goto("/agent/tickets/100", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    const ai = page.getByTestId("ai-panel");
    await ai.waitFor({ state: "visible", timeout: 5000 });
    await ai.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await ai.screenshot({ path: `${OUT}/agent-ai-assist${SUFFIX}.png` });
  } catch (err) {
    console.warn("screenshot 'agent-ai-assist' failed:", err);
  }
  // AI assist with MCP tool results (ticket 108 — server-monitoring scenario):
  // expand the draft body, reveal the tool trace, and open each MCP result card
  // so the live monitoring readings show in the shot.
  try {
    await page.goto("/agent/tickets/108", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    const ai = page.getByTestId("ai-panel");
    await ai.waitFor({ state: "visible", timeout: 5000 });
    await page.getByTestId("ai-panel-draft-toggle-9101").click().catch(() => undefined);
    await page.getByTestId("ai-panel-draft-trace-toggle-9101").click().catch(() => undefined);
    for (const i of [0, 1]) {
      await page
        .getByTestId(`ai-panel-draft-trace-step-9101-${i}`)
        .click()
        .catch(() => undefined);
    }
    await ai.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await ai.screenshot({ path: `${OUT}/agent-ai-mcp${SUFFIX}.png` });
  } catch (err) {
    console.warn("screenshot 'agent-ai-mcp' failed:", err);
  }
  // AI origin trace on an auto-sent reply (ticket 110): the newest article is
  // the one the agent sent itself, so it is selected already — expand its
  // 🤖 badge and open each tool card so the shot shows the marker in the
  // list, the badge in the reader, and the arguments each tool was called
  // with.
  try {
    await page.goto("/agent/tickets/110", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    const badge = page.getByTestId("ai-origin-badge-601");
    await badge.waitFor({ state: "visible", timeout: 5000 });
    await badge.click();
    for (const i of [0, 1, 2]) {
      await page
        .getByTestId(`ai-origin-trace-step-601-${i}`)
        .click()
        .catch(() => undefined);
    }
    // Expanding the trace scrolls the page; bring the article view back to
    // the top so the shot starts at the list + reader, not mid-panel.
    await page
      .getByTestId("article-view-tabs")
      .evaluate((el) => el.scrollIntoView({ block: "start" }))
      .catch(() => undefined);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/agent-ai-origin${SUFFIX}.png`, fullPage: false });
  } catch (err) {
    console.warn("screenshot 'agent-ai-origin' failed:", err);
  }
  // User menu open (best-effort — never fail the run over it)
  try {
    await page.goto("/agent", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await page.waitForTimeout(500);
    await page.locator('[data-testid="account-menu-trigger"]:visible').first().click();
    await page.getByTestId("account-menu").waitFor({ state: "visible", timeout: 3000 });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/user-menu${SUFFIX}.png`, fullPage: false });
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
    // AI subsystem administration.
    ["/admin/ai", "admin-ai-settings"],
    ["/admin/ai/providers", "admin-ai-providers"],
    ["/admin/ai/queues", "admin-ai-queue-policies"],
    ["/admin/ai/audit", "admin-ai-audit"],
  ] as const) {
    await shot(page, route, name);
  }
  // MCP clients with one server's tool list expanded — the per-tool
  // enabled/mutating switches are the point of the page, and the collapsed
  // list shows none of them.
  try {
    await page.goto("/admin/ai/mcp", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await page.getByTestId("admin-ai-mcp-toggle-1").click();
    await page.getByTestId("admin-ai-mcp-tools-1").waitFor({ state: "visible", timeout: 5000 });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/admin-ai-mcp${SUFFIX}.png`, fullPage: false });
  } catch (err) {
    console.warn("screenshot 'admin-ai-mcp' failed:", err);
  }
  // Provider edit form: cost budgets (day/week/month) and the per-provider
  // tool-round override only exist in the form, not in the list row.
  try {
    await page.goto("/admin/ai/providers", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await page.getByTestId("admin-ai-provider-menu-trigger-2").click();
    await page.getByTestId("admin-ai-provider-edit-2").click();
    // Scroll the form to the tool-round field so its help text isn't clipped
    // by the dialog's bottom edge.
    await page.mouse.move(720, 500);
    await page.mouse.wheel(0, 240);
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${OUT}/admin-ai-provider-budget${SUFFIX}.png`, fullPage: false });
  } catch (err) {
    console.warn("screenshot 'admin-ai-provider-budget' failed:", err);
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
