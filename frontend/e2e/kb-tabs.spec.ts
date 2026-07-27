import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("knowledge base tabs (articles / categories)", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("switches to the Categories tab and back", async ({ page }) => {
    await page.goto("/agent/kb");
    await expect(page.getByTestId("kb-article-list")).toBeVisible();

    await page.getByRole("tab", { name: "Categories" }).click();
    await expect(page.getByTestId("kb-categories-page")).toBeVisible();
    await expect(page.getByTestId("kb-category-new")).toBeVisible();
    await expect(page).toHaveURL(/tab=categories/);
    // article-only controls are gone on the categories tab
    await expect(page.getByTestId("kb-state-filter")).toHaveCount(0);

    await page.getByRole("tab", { name: "Articles" }).click();
    await expect(page.getByTestId("kb-article-list")).toBeVisible();
  });

  test("old /agent/kb/categories URL redirects into the Categories tab", async ({
    page,
  }) => {
    await page.goto("/agent/kb/categories");
    await page.waitForURL(/\/agent\/kb\?.*tab=categories/);
    await expect(page.getByTestId("kb-categories-page")).toBeVisible();
  });

  test("+ new category deep link opens the create drawer on the tab", async ({
    page,
  }) => {
    await page.goto("/agent/kb");
    await page.getByTestId("kb-new-category").click();
    await expect(page.getByTestId("kb-categories-page")).toBeVisible();
    await expect(page.getByTestId("kb-category-form-name")).toBeVisible();
  });
});
