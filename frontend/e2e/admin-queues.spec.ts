import { test, expect } from "@playwright/test";
import { mockAdminApi, loginAsAgent } from "./fixtures/mock-admin-api";

test.describe("admin queues", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
    await loginAsAgent(page);
  });

  test("creates a new queue", async ({ page }) => {
    await page.goto("/admin/queues");
    await expect(page.getByTestId("admin-queues-page")).toBeVisible();
    await expect(page.getByTestId("admin-row-1")).toContainText("Raw");

    await page.getByTestId("admin-new-button").click();
    await page.getByTestId("admin-form-name").fill("Support::Escalations");
    await page.getByTestId("admin-form-group_id").fill("1");
    await page.getByTestId("admin-form-system_address_id").fill("1");
    await page.getByTestId("admin-form-salutation_id").fill("1");
    await page.getByTestId("admin-form-signature_id").fill("1");
    await page.getByTestId("admin-form-follow_up_id").fill("1");
    await page.getByTestId("admin-form-submit").click();

    await expect(page.getByTestId("admin-form")).not.toBeVisible();
    await expect(page.getByText("Support::Escalations")).toBeVisible();
  });
});
