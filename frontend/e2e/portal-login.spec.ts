import { test, expect } from "@playwright/test";
import { mockPortalApi } from "./fixtures/mock-portal-api";

test.describe("portal login flow", () => {
  test.beforeEach(async ({ page }) => {
    await mockPortalApi(page);
  });

  test("rejects bad credentials", async ({ page }) => {
    await page.goto("/portal/login");
    await page.getByTestId("portal-login-username").fill("customer@example.com");
    await page.getByTestId("portal-login-password").fill("wrong");
    await page.getByTestId("portal-login-submit").click();
    await expect(page.getByTestId("portal-login-error")).toBeVisible();
    await expect(page).toHaveURL(/\/portal\/login/);
  });

  test("logs in and shows the ticket list", async ({ page }) => {
    await page.goto("/portal/login");
    await page.getByTestId("portal-login-username").fill("customer@example.com");
    await page.getByTestId("portal-login-password").fill("secret");
    await page.getByTestId("portal-login-submit").click();
    await page.waitForURL(/\/portal$/);
    await expect(page.getByTestId("portal-ticket-list-page")).toBeVisible();
    await expect(page.getByTestId("portal-current-customer")).toContainText("Cara");
  });

  test("redirects unauthenticated /portal to portal login", async ({ page }) => {
    await page.goto("/portal/tickets/new");
    await page.waitForURL(/\/portal\/login/);
    await expect(page.getByTestId("portal-login-form")).toBeVisible();
  });

  test("logs out back to portal login", async ({ page }) => {
    await page.goto("/portal/login");
    await page.getByTestId("portal-login-username").fill("customer@example.com");
    await page.getByTestId("portal-login-password").fill("secret");
    await page.getByTestId("portal-login-submit").click();
    await page.waitForURL(/\/portal$/);
    await page.getByTestId("portal-logout-btn").click();
    await page.waitForURL(/\/portal\/login/);
  });
});
