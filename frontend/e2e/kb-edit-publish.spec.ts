import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("knowledge base editor", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("edits an article and saves", async ({ page }) => {
    await page.goto("/agent/kb/700/edit");
    await expect(page.getByTestId("kb-editor-page")).toBeVisible();
    await expect(page.getByTestId("kb-form-title")).toHaveValue(
      "Printer offline troubleshooting",
    );

    await page.getByTestId("kb-form-content").fill(
      "# Printer offline\n\nUpdated: check power, network, and toner.",
    );
    await expect(page.getByTestId("kb-editor-preview")).toContainText(
      "Updated: check power, network, and toner.",
    );

    await page.getByTestId("kb-form-submit").click();
    await page.waitForURL(/\/agent\/kb\/700$/);
    await expect(page.getByTestId("kb-article-page")).toBeVisible();
  });

  test("publishes an article after confirmation", async ({ page }) => {
    await page.goto("/agent/kb/700/edit");
    await expect(page.getByTestId("kb-publish-button")).toBeEnabled();
    await page.getByTestId("kb-publish-button").click();
    await expect(page.getByText("Publish article?")).toBeVisible();
    await page.getByTestId("kb-publish-confirm").click();
    await expect(page.getByText("Publish article?")).not.toBeVisible();
    await expect(page.getByTestId("kb-publish-button")).toBeDisabled();
  });

  test("creates a new article", async ({ page }) => {
    await page.goto("/agent/kb/new");
    await expect(page.getByTestId("kb-editor-page")).toBeVisible();
    await page.getByTestId("kb-form-title").fill("New FAQ entry");
    await page.getByTestId("kb-form-slug").fill("new-faq-entry");
    await page.getByTestId("kb-form-content").fill("# New FAQ\n\nAnswer text.");
    await page.getByTestId("kb-form-submit").click();
    await page.waitForURL(/\/agent\/kb\/701$/);
  });
});
