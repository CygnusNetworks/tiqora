import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("stats / reports page", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("renders tiles, charts, and the agent workload table", async ({ page }) => {
    await page.goto("/agent/stats");
    await expect(page.getByTestId("stats-page")).toBeVisible();

    await expect(page.getByTestId("stats-tile-total")).toContainText("5");
    await expect(page.getByTestId("stats-tile-escalated")).toContainText("1");

    await expect(page.getByTestId("stats-chart-volume")).toBeVisible();
    await expect(page.getByTestId("stats-chart-backlog")).toBeVisible();
    await expect(page.getByTestId("stats-chart-open-snapshot")).toBeVisible();

    await expect(page.getByTestId("stats-workload-table")).toBeVisible();
    await expect(page.getByTestId("stats-workload-table")).toContainText("agent");
    await expect(page.getByTestId("stats-workload-table")).toContainText("Ada Agent");
  });

  test("navigates to stats from the sidebar", async ({ page }) => {
    await page.goto("/agent");
    await page.getByTestId("agent-nav-stats").click();
    await page.waitForURL(/\/agent\/stats/);
    await expect(page.getByTestId("stats-page")).toBeVisible();
  });
});
