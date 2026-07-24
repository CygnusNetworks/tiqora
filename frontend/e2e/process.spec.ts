import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";
import { mockAdminApi } from "./fixtures/mock-admin-api";

test.describe("ticket process widget", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("starts a process, submits its dialog, and reflects the new activity", async ({
    page,
  }) => {
    await page.goto("/agent/tickets/100");
    await expect(page.getByTestId("ticket-zoom")).toBeVisible();
    // Ticket 100 starts outside any process; the start trigger lives in the
    // ticket-zoom ⋯ overflow menu (the inline affordance was removed).
    await page.getByTestId("ticket-zoom-overflow-trigger").click();
    await page.getByTestId("overflow-start-process").click();
    // `process-start-select` is a custom SelectField, not a native <select>;
    // force the option click past the portal's entrance animation.
    await page.getByTestId("process-start-select").click();
    await page.getByTestId("process-start-select-menu-option-Process-1").click({ force: true });
    await page.getByTestId("process-start-submit").click();

    await expect(page.getByTestId("process-widget-activity-name")).toHaveText("Collect info");
    await page.getByTestId("process-dialog-button-ActivityDialog-1").click();
    await expect(page.getByTestId("process-dialog-form")).toBeVisible();

    await page.getByTestId("process-field-Title-input").fill("Updated via e2e");
    await page.getByTestId("process-dialog-submit").click();

    await expect(page.getByTestId("process-widget-activity-name")).toHaveText("Done");
  });
});

test.describe("admin processes (read-only)", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
    await loginAsAgent(page);
  });

  test("lists processes and drills into a process's activities", async ({ page }) => {
    await page.goto("/admin/processes");
    await expect(page.getByTestId("admin-processes-page")).toBeVisible();
    await expect(page.getByTestId("process-link-Process-1")).toContainText("Onboarding");

    await page.getByTestId("process-link-Process-1").click();
    await expect(page.getByTestId("admin-process-detail-page")).toBeVisible();
    await expect(page.getByTestId("process-activity-Activity-a")).toContainText("Collect info");
  });
});
