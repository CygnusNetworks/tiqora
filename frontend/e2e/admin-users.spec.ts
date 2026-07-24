import { test, expect } from "@playwright/test";
import { mockAdminApi, loginAsAgent } from "./fixtures/mock-admin-api";

test.describe("admin users", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
    await loginAsAgent(page);
  });

  test("lists, creates, edits, and deactivates a user", async ({ page }) => {
    await page.goto("/admin/users");
    await expect(page.getByTestId("admin-users-page")).toBeVisible();
    await expect(page.getByTestId("admin-row-1")).toContainText("agent");

    // Create
    await page.getByTestId("admin-new-button").click();
    await page.getByTestId("admin-form-login").fill("bob");
    await page.getByTestId("admin-form-password").fill("s3cret!");
    await page.getByTestId("admin-form-first_name").fill("Bob");
    await page.getByTestId("admin-form-last_name").fill("Builder");
    await page.getByTestId("admin-form-submit").click();
    await expect(page.getByTestId("admin-form")).not.toBeVisible();
    await expect(page.getByText("Bob Builder")).toBeVisible();

    // Edit — row actions now live behind a ⋯ menu; the menu items render in a
    // portal, so click them off the page (only one menu is open at a time).
    const newRow = page.locator('[data-testid^="admin-row-"]', { hasText: "Bob Builder" });
    await newRow.getByTestId(/admin-row-menu-trigger-/).click();
    await page.getByTestId(/admin-row-edit-/).click();
    await expect(page.getByTestId("admin-form-first_name")).toHaveValue("Bob");
    await page.getByTestId("admin-form-first_name").fill("Robert");
    await page.getByTestId("admin-form-submit").click();
    await expect(page.getByTestId("admin-form")).not.toBeVisible();
    await expect(page.getByText("Robert Builder")).toBeVisible();

    // Deactivate
    const editedRow = page.locator('[data-testid^="admin-row-"]', { hasText: "Robert Builder" });
    await editedRow.getByTestId(/admin-row-menu-trigger-/).click();
    await page.getByTestId(/admin-row-deactivate-/).click();
    await expect(editedRow.getByText("Invalid")).toBeVisible();
  });

  test("resets a user's 2FA from the row menu", async ({ page }) => {
    await page.goto("/admin/users");
    const row = page.locator('[data-testid^="admin-row-"]', { hasText: "agent" });
    await row.getByTestId(/admin-row-menu-trigger-/).click();
    await page.getByTestId("admin-row-reset2fa-1").click();
    // Confirmation guards the destructive reset.
    await expect(page.getByTestId("confirm-dialog")).toBeVisible();
    await page.getByTestId("confirm-dialog-confirm").click();
    await expect(page.getByTestId("confirm-dialog")).not.toBeVisible();
  });

  test("requires the required fields before submitting", async ({ page }) => {
    await page.goto("/admin/users");
    await page.getByTestId("admin-new-button").click();
    await page.getByTestId("admin-form-submit").click();
    await expect(page.getByTestId("admin-form")).toBeVisible();
  });
});
