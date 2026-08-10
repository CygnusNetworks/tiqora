import { test, expect } from "@playwright/test";
import { mockApi } from "./fixtures/mock-api";

test.describe("customer portal switched off", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    // Registered after mockApi: Playwright prefers the most recently added
    // matching handler, so this overrides the shared discovery response.
    await page.route("**/api/v1/auth/methods", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          password: true,
          oidc: false,
          spnego: false,
          ldap: false,
          webauthn: false,
          portal_enabled: false,
        }),
      }),
    );
  });

  test("sends the start page straight to the agent login", async ({ page }) => {
    await page.goto("/");
    await page.waitForURL(/\/login/);
    await expect(page.getByTestId("login-submit")).toBeVisible();
  });

  test("keeps the portal itself out of reach", async ({ page }) => {
    await page.goto("/portal");
    await page.waitForURL(/\/login/);
    await expect(page.getByTestId("login-submit")).toBeVisible();
  });
});
