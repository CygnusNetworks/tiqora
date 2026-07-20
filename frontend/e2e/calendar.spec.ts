import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("agent calendar", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("renders month grid with calendar switcher and appointment", async ({ page }) => {
    await page.goto("/agent/calendar");
    await expect(page.getByTestId("calendar-page")).toBeVisible();
    await expect(page.getByTestId("calendar-switcher")).toBeVisible();
    await expect(page.getByTestId("calendar-toggle-1")).toBeVisible();
    await expect(page.getByTestId("calendar-month-grid")).toBeVisible();
    await expect(page.getByTestId("calendar-occurrence-900")).toContainText(
      "Sprint planning",
    );
  });

  test("switches between month, week, and agenda views", async ({ page }) => {
    await page.goto("/agent/calendar");
    await expect(page.getByTestId("calendar-month-grid")).toBeVisible();

    await page.getByTestId("calendar-view-week").click();
    await expect(page.getByTestId("calendar-week-view")).toBeVisible();

    await page.getByTestId("calendar-view-agenda").click();
    await expect(page.getByTestId("calendar-agenda-view")).toBeVisible();
  });

  test("opens the new appointment dialog", async ({ page }) => {
    await page.goto("/agent/calendar");
    await page.getByTestId("calendar-new-appointment").click();
    await expect(page.getByTestId("appointment-form")).toBeVisible();
    await expect(page.getByTestId("appointment-title")).toBeVisible();
  });

  test("opens the edit dialog from an occurrence and can delete it", async ({ page }) => {
    await page.goto("/agent/calendar");
    await page.getByTestId("calendar-occurrence-900").click();
    await expect(page.getByTestId("appointment-form")).toBeVisible();
    await expect(page.getByTestId("appointment-title")).toHaveValue("Sprint planning");
    await page.getByTestId("appointment-delete").click();
    await expect(page.getByTestId("appointment-form")).not.toBeVisible();
  });
});
