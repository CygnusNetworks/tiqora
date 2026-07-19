import { test, expect } from "@playwright/test";
import { mockAdminApi, loginAsAgent } from "./fixtures/mock-admin-api";

test.describe("admin access guard", () => {
  test("renders access denied for a 403 on the capability probe", async ({ page }) => {
    await mockAdminApi(page);
    await loginAsAgent(page);

    // Override the capability-probe endpoint (adminGroups.list) to 403 —
    // registered after mockAdminApi's handler so it takes precedence.
    await page.route("**/api/v1/admin/groups", async (route) => {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Admin privileges required" }),
      });
    });

    await page.goto("/admin/users");
    await expect(page.getByTestId("admin-access-denied")).toBeVisible();
  });
});
