import { test, expect } from "@playwright/test";
import { mockPortalApi, loginAsCustomer } from "./fixtures/mock-portal-api";

test.describe("portal knowledge base", () => {
  test.beforeEach(async ({ page }) => {
    await mockPortalApi(page);
    await loginAsCustomer(page);
  });

  test("shows a hint before any search", async ({ page }) => {
    await page.goto("/portal/kb");
    await expect(page.getByTestId("portal-kb-search-page")).toBeVisible();
    await expect(page.getByText("Search our help articles")).toBeVisible();
  });

  test("searches and opens an article", async ({ page }) => {
    await page.goto("/portal/kb");
    await page.getByTestId("portal-kb-search-input").fill("password");
    await page.getByTestId("portal-kb-search-submit").click();
    await expect(page.getByTestId("portal-kb-hit-50")).toBeVisible();
    await page.getByTestId("portal-kb-hit-50").click();
    await page.waitForURL(/\/portal\/kb\/50/);
    await expect(page.getByTestId("portal-kb-article-page")).toBeVisible();
    await expect(page.getByTestId("portal-kb-article-body")).toContainText(
      "Forgot password",
    );
  });

  test("shows no-results empty state", async ({ page }) => {
    await page.goto("/portal/kb");
    await page.getByTestId("portal-kb-search-input").fill("xyzzy-nomatch");
    await page.getByTestId("portal-kb-search-submit").click();
    await expect(page.getByTestId("portal-kb-search-empty")).toBeVisible();
  });
});
