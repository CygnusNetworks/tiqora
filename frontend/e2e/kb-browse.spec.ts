import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("knowledge base browse", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("browses category tree into article list and opens the reader", async ({
    page,
  }) => {
    await page.goto("/agent/kb");
    await expect(page.getByTestId("kb-page")).toBeVisible();
    await expect(page.getByTestId("category-tree")).toBeVisible();
    await expect(page.getByTestId("category-node-1")).toContainText("General");
    await expect(page.getByTestId("category-node-2")).toContainText("Printers");

    await expect(page.getByTestId("kb-article-list")).toBeVisible();
    await expect(page.getByTestId("kb-article-700")).toContainText(
      "Printer offline troubleshooting",
    );

    await page.getByTestId("category-node-2").click();
    await expect(page.getByTestId("kb-article-700")).toBeVisible();

    await page.getByTestId("kb-article-700").click();
    await page.waitForURL(/\/agent\/kb\/700/);
    await expect(page.getByTestId("kb-article-page")).toBeVisible();
    await expect(page.getByTestId("kb-article-body")).toContainText(
      "Check the power cable",
    );
  });

  test("navigates via the topbar knowledge base link", async ({ page }) => {
    await page.goto("/agent");
    await page.getByRole("link", { name: "Knowledge base" }).click();
    await page.waitForURL(/\/agent\/kb$/);
    await expect(page.getByTestId("kb-page")).toBeVisible();
  });
});
