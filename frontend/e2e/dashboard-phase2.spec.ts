import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent, useGermanUi as germanUi } from "./fixtures/mock-api";

// Phase 2 of the dashboards: filters, report widgets, drill-down, template updates, TV mode and the
// public wallboard. Everything runs against the stateful demo mock (R-DASH-E2E-STORE); like
// dashboard.spec.ts there is no toHaveScreenshot baseline (R-DASH-E2E-VISUAL): layout invariants
// plus `page.screenshot` artefacts.
test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await germanUi(page);
  await mockApi(page);
  await loginAsAgent(page);
});

test("a queue filter narrows the counters and survives a reload", async ({ page }) => {
  await page.goto("/agent");
  const open = page.getByTestId("widget-demo-w-open");
  await expect(open).toContainText("12");
  await page.getByRole("button", { name: /^Queues: Alle/ }).click();
  await page.getByRole("checkbox", { name: "Raw::Misc" }).check();
  await page.getByRole("button", { name: "Übernehmen" }).click();
  await expect(page.getByRole("button", { name: "Queues: Raw::Misc" })).toBeVisible();
  // Queue 2 holds 5 of the 6 open demo tickets: the tile no longer says 12.
  await expect(open).not.toContainText("12");
  await expect(open).toContainText("5");
  await page.reload();
  await expect(page.getByRole("button", { name: "Queues: Raw::Misc" })).toBeVisible();
  await expect(page.getByTestId("widget-demo-w-open")).toContainText("5");
  // Back to all queues.
  await page.getByRole("button", { name: "Queues: Raw::Misc" }).click();
  await page.getByRole("button", { name: "Zurücksetzen" }).click();
  await expect(page.getByTestId("widget-demo-w-open")).toContainText("12");
});

test("add a report widget from the Berichte group and choose a report", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "Anpassen" }).click();
  await page.getByRole("button", { name: /^Bericht/ }).first().click();
  const dlg = page.getByRole("dialog", { name: "Einstellungen" });
  await expect(dlg).toBeVisible();
  await dlg.getByTestId("report-settings-report").selectOption({ index: 1 });
  await dlg.getByRole("button", { name: "Übernehmen" }).click();
  const tile = page.locator('[data-type="core.report"]');
  await expect(tile).toHaveCount(1);
  await expect(tile.getByText("Wählen Sie einen Bericht in den Einstellungen.")).toHaveCount(0);
  await page.getByRole("button", { name: "Speichern" }).click();
  await expect(page.getByRole("toolbar", { name: "Bearbeiten" })).toHaveCount(0);
  await page.reload();
  await expect(page.locator('[data-type="core.report"]')).toHaveCount(1);
});

test("a counter drills down to its tickets and 'Als Suche speichern' fills the editor", async ({ page }) => {
  await page.goto("/agent");
  await page.getByTestId("widget-demo-w-open").getByRole("link", { name: /Tickets zu/ }).first().click();
  await expect(page).toHaveURL(/\/agent\/tickets\/drill\?/);
  await expect(page.getByTestId("drill-title")).toContainText("Meine offenen Tickets");
  await expect(page.getByTestId("drill-total")).toHaveText("6");
  await page.getByTestId("drill-save-search").click();
  await expect(page).toHaveURL(/\/agent\/searches\/new/);
  await expect(page.getByTestId("saved-search-editor")).toBeVisible();
  await expect(page.getByTestId("saved-search-editor-name")).toHaveValue("Meine offenen Tickets");
  // The two conditions of the drill filter arrive filled in (owner and state).
  await expect(page.getByTestId("saved-search-editor").getByText(/Besitzer|Owner/i).first()).toBeVisible();
});

test("a template update banner merges the new widgets", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("tab", { name: "Teamvorlage" }).click();
  const banner = page.getByRole("status").filter({ hasText: "Neue Version der Vorlage „Support-Team“" });
  await expect(banner).toContainText("2 neue Widgets");
  await expect(page.locator('[data-type="core.sla-gauge"]')).toHaveCount(0);
  await banner.getByRole("button", { name: "Übernehmen" }).click();
  await expect(page.locator('[data-type="core.sla-gauge"]')).toHaveCount(1);
  await expect(page.locator('[data-type="core.chat-queue"]')).toHaveCount(1);
  await expect(banner).toHaveCount(0);
});

test("TV mode rotates between two dashboards", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-10T07:00:00Z") });
  await page.goto("/agent/wallboard?ids=1,3&rotate=15");
  const board = page.getByTestId("wallboard");
  await expect(board).toBeVisible();
  await expect(page.getByTestId("wallboard-header")).toContainText("Mein Start");
  await expect(page.getByTestId("wallboard-header")).toContainText("1 / 2");
  await page.clock.fastForward(16_000);
  await expect(page.getByTestId("wallboard-header")).toContainText("Teamvorlage");
  await expect(page.getByTestId("wallboard-header")).toContainText("2 / 2");
  await page.clock.fastForward(16_000);
  await expect(page.getByTestId("wallboard-header")).toContainText("Mein Start");
});

test("the public wallboard shows the four KPI tiles without any link", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/wallboard#demo-kiosk-token");
  const board = page.getByTestId("wallboard");
  await expect(board).toBeVisible();
  const tiles = board.locator('[data-type="core.counter"]');
  await expect(tiles).toHaveCount(4);
  for (let i = 0; i < 4; i++) await expect(tiles.nth(i)).toHaveCSS("grid-column-end", "span 3");
  await expect(page.locator("a")).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath("wallboard-kiosk-1920.png") });
});

test("an invalid wallboard link shows the message and no data", async ({ page }) => {
  await page.goto("/wallboard#invalid-token");
  await expect(page.getByTestId("kiosk-notice")).toHaveText("Dieser Wallboard-Link ist ungültig oder abgelaufen.");
  await expect(page.getByTestId("wallboard")).toHaveCount(0);
});
