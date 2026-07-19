import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("knowledge base reader", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("shows breadcrumbs, current content, and version history", async ({
    page,
  }) => {
    await page.goto("/agent/kb/700");
    await expect(page.getByTestId("kb-article-page")).toBeVisible();
    await expect(page.getByTestId("kb-article-body")).toContainText(
      "Check the power cable and network link.",
    );

    const breadcrumbs = page.getByLabel("Breadcrumbs");
    await expect(breadcrumbs).toContainText("General");
    await expect(breadcrumbs).toContainText("Printers");

    await expect(page.getByTestId("kb-version-history")).toBeVisible();
    await expect(page.getByTestId("kb-version-2")).toBeVisible();
    await expect(page.getByTestId("kb-version-1")).toBeVisible();
  });

  test("views a past version read-only", async ({ page }) => {
    await page.goto("/agent/kb/700");
    await page.getByTestId("kb-version-1").click();
    await expect(page.getByTestId("kb-version-banner")).toBeVisible();
    await expect(page.getByTestId("kb-article-body")).toContainText(
      "Restart the printer.",
    );

    await page.getByText("View current").click();
    await expect(page.getByTestId("kb-version-banner")).not.toBeVisible();
    await expect(page.getByTestId("kb-article-body")).toContainText(
      "Check the power cable and network link.",
    );
  });
});
