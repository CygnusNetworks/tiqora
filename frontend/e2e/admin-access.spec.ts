import { test, expect } from "@playwright/test";
import { mockAdminApi, loginAsAgent } from "./fixtures/mock-admin-api";

test.describe("admin access guard", () => {
  test("renders access denied when /me reports is_admin=false", async ({ page }) => {
    await mockAdminApi(page);

    // Override /me (and login) so the agent is a non-admin. RequireAdmin now
    // uses UserMe.is_admin instead of probing adminGroups.list.
    await page.route("**/api/v1/auth/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: 1,
          login: "agent",
          first_name: "Ada",
          last_name: "Agent",
          auth_method: "password",
          is_admin: false,
        }),
      });
    });
    await page.route("**/api/v1/auth/login", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          user: {
            id: 1,
            login: "agent",
            first_name: "Ada",
            last_name: "Agent",
            auth_method: "password",
            is_admin: false,
          },
        }),
      });
    });

    await loginAsAgent(page);
    await page.goto("/admin/users");
    await expect(page.getByTestId("admin-access-denied")).toBeVisible();
  });
});
