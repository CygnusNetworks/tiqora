import { test, expect, type Page } from "@playwright/test";
import { mockAdminApi, loginAsAgent } from "./fixtures/mock-admin-api";

// The CrudDrawer renders selects as portal listboxes (not native <select>):
// open the trigger, then force-click the option (the entrance animation makes
// the portal briefly fail Playwright's stability check).
async function selectAdminOption(page: Page, fieldTestId: string, value: string) {
  await page.getByTestId(fieldTestId).click();
  await page.getByTestId(`${fieldTestId}-menu-option-${value}`).click({ force: true });
}

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
    await selectAdminOption(page, "admin-form-group_id", "1");
    await selectAdminOption(page, "admin-form-system_address_id", "1");
    await selectAdminOption(page, "admin-form-salutation_id", "1");
    await selectAdminOption(page, "admin-form-signature_id", "1");
    await selectAdminOption(page, "admin-form-follow_up_id", "1");
    await page.getByTestId("admin-form-submit").click();

    await expect(page.getByTestId("admin-form")).not.toBeVisible();
    await expect(page.getByText("Support::Escalations")).toBeVisible();
  });
});
