import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("queue view", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("renders queue tree and ticket table", async ({ page }) => {
    await page.goto("/agent/queues");
    await expect(page.getByTestId("queues-page")).toBeVisible();
    await expect(page.getByTestId("queue-tree")).toBeVisible();
    await expect(page.getByTestId("queue-node-1")).toBeVisible();
    await expect(page.getByTestId("ticket-table")).toBeVisible();
    await expect(page.getByTestId("ticket-row-100")).toContainText(
      "202607191000001",
    );
    await expect(page.getByTestId("ticket-row-100")).toContainText("Printer");
  });

  test("opens ticket zoom from table row", async ({ page }) => {
    await page.goto("/agent/queues");
    await page.getByTestId("ticket-row-100").click();
    await page.waitForURL(/\/agent\/tickets\/100/);
    await expect(page.getByTestId("ticket-zoom")).toBeVisible();
    await expect(page.getByTestId("ticket-header")).toContainText(
      "202607191000001",
    );
  });
});
