import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent, useGermanUi } from "./fixtures/mock-api";

test.describe("saved searches", () => {
  test.beforeEach(async ({ page }) => {
    await useGermanUi(page);
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("save the current queue view and open it from the sidebar", async ({ page }) => {
    await page.goto("/agent/queues?state_type=pending");
    await page.getByTestId("queues-save-view").click();
    await page.getByTestId("save-view-name").fill("Wartend");
    // The switch input is visually hidden: click its label, then check the state.
    await page.getByTestId("save-view-count").locator("xpath=..").click();
    await expect(page.getByTestId("save-view-count")).toBeChecked();
    const created = page.waitForResponse((r) => r.url().endsWith("/api/v1/saved-searches") && r.request().method() === "POST");
    await page.getByTestId("save-view-confirm").click();
    const made = await created;
    // The queue filter travels as list_params and is stored as criteria.
    expect(made.request().postDataJSON()).toMatchObject({ list_params: { state_type: "pending" }, show_in_sidebar: true, show_count: true });
    const saved = (await made.json()) as { id: number; criteria: unknown };
    expect(saved.criteria).toEqual({ version: 1, where: { all: [{ field: "state_type", op: "in", value: ["pending"] }] } });
    const entry = page.getByTestId(/^sidebar-search-\d+$/).filter({ hasText: "Wartend" });
    await expect(entry).toBeVisible();
    await expect(page.getByTestId(`sidebar-search-${saved.id}-count`)).toHaveText("5");
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
