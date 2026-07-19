import { test, expect } from "@playwright/test";
import { mockPortalApi, loginAsCustomer } from "./fixtures/mock-portal-api";

test.describe("portal ticket list and detail", () => {
  test.beforeEach(async ({ page }) => {
    await mockPortalApi(page);
    await loginAsCustomer(page);
  });

  test("lists tickets and filters by state", async ({ page }) => {
    await expect(page.getByTestId("portal-ticket-list")).toBeVisible();
    await expect(page.getByTestId("portal-ticket-200")).toContainText(
      "Cannot log in to the client portal",
    );
    await expect(page.getByTestId("portal-ticket-201")).toContainText(
      "Invoice question",
    );

    await page.getByRole("tab", { name: "Closed" }).click();
    await expect(page.getByTestId("portal-ticket-201")).toBeVisible();
    await expect(page.getByTestId("portal-ticket-200")).toHaveCount(0);
  });

  test("opens a ticket, shows the thread, and sends a reply", async ({ page }) => {
    await page.getByTestId("portal-ticket-200").click();
    await page.waitForURL(/\/portal\/tickets\/200/);
    await expect(page.getByTestId("portal-ticket-detail-page")).toBeVisible();
    await expect(page.getByTestId("portal-article-thread")).toBeVisible();
    await expect(page.getByTestId("portal-article-900")).toBeVisible();
    await expect(page.getByTestId("portal-article-901")).toBeVisible();

    await page.getByTestId("portal-reply-body").fill("Thanks, still broken though.");
    await page.getByTestId("portal-reply-submit").click();

    await expect(page.getByText("Thanks, still broken though.")).toBeVisible();
  });

  test("creates a new ticket", async ({ page }) => {
    await page.getByTestId("portal-new-ticket-link").click();
    await page.waitForURL(/\/portal\/tickets\/new/);
    await page.getByTestId("portal-new-ticket-subject").fill("My printer is on fire");
    await page
      .getByTestId("portal-new-ticket-body")
      .fill("Smoke is coming out of the tray, please help.");
    await page.getByTestId("portal-new-ticket-submit").click();
    await page.waitForURL(/\/portal\/tickets\/300/);
    await expect(page.getByTestId("portal-ticket-detail-page")).toBeVisible();
  });
});

test.describe("portal ticket list — empty state", () => {
  test("shows helpful empty state copy when there are no tickets", async ({ page }) => {
    await mockPortalApi(page);
    await page.route("**/api/portal/tickets?**", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0, offset: 0, limit: 100 }),
      });
    });
    await loginAsCustomer(page);
    await expect(page.getByTestId("portal-ticket-list-empty")).toBeVisible();
  });
});
