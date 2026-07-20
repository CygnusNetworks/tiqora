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
    await expect(page.getByTestId("process-widget")).toBeVisible();
    await expect(page.getByTestId("process-widget-inactive")).toBeVisible();

    await page.getByTestId("process-widget-start-button").click();
    await page.getByTestId("process-start-select").selectOption("Process-1");
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
