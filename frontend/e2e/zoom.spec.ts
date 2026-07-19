import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("ticket zoom", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("shows sanitized article body, external banner, and attachments", async ({
    page,
  }) => {
    await page.goto("/agent/tickets/100");
    await expect(page.getByTestId("ticket-zoom")).toBeVisible();
    await expect(page.getByTestId("ticket-header")).toContainText("Printer");

    // Expand first article
    await page.getByTestId("article-500").getByRole("button").first().click();

    await expect(page.getByTestId("article-body-html")).toBeVisible();
    await expect(page.getByTestId("article-body-iframe")).toBeVisible();
    await expect(page.getByTestId("external-images-banner")).toBeVisible();
    await expect(page.getByTestId("attachment-list")).toBeVisible();
    await expect(page.getByTestId("attachment-900")).toContainText(
      "screenshot.png",
    );

    // History tab
    await page.getByRole("tab", { name: /history|historie/i }).click();
    await expect(page.getByTestId("history-table")).toBeVisible();
  });
});
