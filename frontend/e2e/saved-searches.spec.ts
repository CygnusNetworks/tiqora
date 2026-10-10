import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("saved searches", () => {
  test.beforeEach(async ({ page }) => {
    // German UI copy: the app reads its language from localStorage.
    await page.addInitScript(() => {
      try {
        localStorage.setItem("tiqora-lang", "de");
      } catch {
        /* no storage: English copy */
      }
    });
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("save the current queue view and open it from the sidebar", async ({ page }) => {
    await page.goto("/agent/queues?state_type=pending");
    await page.getByTestId("queues-save-view").click();
    await page.getByTestId("save-view-name").fill("Wartend");
    await page.getByTestId("save-view-confirm").click();
    const entry = page.getByTestId(/^sidebar-search-\d+$/).filter({ hasText: "Wartend" });
    await expect(entry).toBeVisible();
    await entry.click();
    await expect(page).toHaveURL(/\/agent\/searches\/\d+/);
    await expect(page.getByTestId("ticket-table")).toBeVisible();
  });

  test("import Znuny search profiles", async ({ page }) => {
    await page.goto("/agent/queues");
    await page.getByTestId("sidebar-searches-all").click();
    await page.getByTestId("sidebar-searches-import-znuny").click();
    // A profile that maps to nothing cannot be picked; a partly mapped one is not preselected.
    await expect(page.getByTestId("znuny-import-row-Volltext-check")).toBeDisabled();
    await expect(page.getByTestId("znuny-import-row-Gemischt-check")).not.toBeChecked();
    await expect(page.getByTestId("znuny-import-row-Offen in Support-check")).toBeChecked();
    await page.getByTestId("znuny-import-apply").click();
    await expect(page.getByTestId("znuny-import-result")).toContainText("1 angelegt");
  });
});
