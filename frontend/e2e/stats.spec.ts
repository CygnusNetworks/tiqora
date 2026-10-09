import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("reports page", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("renders key figures, charts, the agent table and the catalogue", async ({ page }) => {
    await page.goto("/agent/reports");
    await expect(page.getByTestId("reports-page")).toBeVisible();

    await expect(page.getByTestId("stats-kpis-kpi-tickets")).toBeVisible();
    await expect(page.getByTestId("stats-chart-volume-chart")).toBeVisible();
    await expect(page.getByTestId("stats-chart-backlog-chart")).toBeVisible();
    await expect(page.getByTestId("stats-chart-open-snapshot-chart")).toBeVisible();

    await expect(page.getByTestId("stats-workload-table")).toBeVisible();
    await expect(page.getByTestId("stats-workload-table")).toContainText("Anna Beispiel");

    await expect(page.getByTestId("reports-card-builtin.sla_compliance")).toBeVisible();
  });

  test("opens a report from the catalogue and shows its table", async ({ page }) => {
    await page.goto("/agent/reports");
    await page.getByTestId("reports-card-builtin.volume").click();
    await page.waitForURL(/\/agent\/reports\/builtin\.volume/);
    await expect(page.getByTestId("report-view-page")).toBeVisible();
    await expect(page.getByTestId("report-view-chart")).toBeVisible();
    await page.getByTestId("report-view-toggle-table").click();
    await expect(page.getByTestId("report-view-table")).toBeVisible();
  });

  test("the old stats address redirects to the reports", async ({ page }) => {
    await page.goto("/agent/stats");
    await page.waitForURL(/\/agent\/reports$/);
    await expect(page.getByTestId("reports-page")).toBeVisible();
  });

  test("navigates to the reports from the sidebar", async ({ page }) => {
    await page.goto("/agent");
    await page.getByTestId("agent-nav-stats").click();
    await page.waitForURL(/\/agent\/reports/);
    await expect(page.getByTestId("reports-page")).toBeVisible();
  });
});
