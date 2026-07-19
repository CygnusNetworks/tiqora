import { test, expect } from "@playwright/test";
import { mockApi } from "./fixtures/mock-api";

test.describe("login flow", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("rejects bad credentials", async ({ page }) => {
    await page.goto("/login");
    await page.getByTestId("login-username").fill("agent");
    await page.getByTestId("login-password").fill("wrong");
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("login-error")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("logs in and shows current user on agent dashboard", async ({
    page,
  }) => {
    await page.goto("/login");
    await page.getByTestId("login-username").fill("agent");
    await page.getByTestId("login-password").fill("secret");
    await page.getByTestId("login-submit").click();
    await page.waitForURL(/\/agent/);
    await expect(page.getByTestId("dashboard")).toBeVisible();
    await expect(page.getByTestId("current-user")).toContainText("Ada");
  });

  test("redirects unauthenticated /agent to login", async ({ page }) => {
    await page.goto("/agent/queues");
    await page.waitForURL(/\/login/);
    await expect(page.getByTestId("login-form")).toBeVisible();
  });
});
