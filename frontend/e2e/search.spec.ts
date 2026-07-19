import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("search", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("header search navigates to results and opens ticket", async ({
    page,
  }) => {
    await page.goto("/agent");
    await page.getByTestId("header-search").fill("printer");
    await page.getByTestId("header-search").press("Enter");
    await page.waitForURL(/\/agent\/search\?.*q=printer/);
    await expect(page.getByTestId("search-page")).toBeVisible();
    await expect(page.getByTestId("search-results")).toBeVisible();
    await expect(page.getByTestId("search-hit-100")).toContainText("Printer");
    await page.getByTestId("search-hit-100").click();
    await page.waitForURL(/\/agent\/tickets\/100/);
    await expect(page.getByTestId("ticket-zoom")).toBeVisible();
  });
});
