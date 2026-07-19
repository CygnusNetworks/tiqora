import { test, expect } from "@playwright/test";
import { mockAdminApi, loginAsAgent } from "./fixtures/mock-admin-api";

test.describe("admin dynamic fields", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
    await loginAsAgent(page);
  });

  test("creates a dropdown field with a possible-values config", async ({ page }) => {
    await page.goto("/admin/dynamic-fields");
    await expect(page.getByTestId("admin-dynamic-fields-page")).toBeVisible();
    await expect(page.getByTestId("admin-row-1")).toContainText("Process");

    await page.getByTestId("admin-new-button").click();
    await page.getByTestId("dynamic-field-name").fill("Priority Bucket");
    await page.getByTestId("dynamic-field-label").fill("Priority bucket");
    await page.getByTestId("dynamic-field-order").fill("2");
    await page.getByTestId("dynamic-field-type").selectOption("Dropdown");

    await expect(page.getByTestId("dynamic-field-config-select")).toBeVisible();
    await page.getByTestId("dynamic-field-option-add").click();
    await page.getByTestId("dynamic-field-option-key-0").fill("low");
    await page.getByTestId("dynamic-field-option-label-0").fill("Low");
    await page.getByTestId("dynamic-field-option-add").click();
    await page.getByTestId("dynamic-field-option-key-1").fill("high");
    await page.getByTestId("dynamic-field-option-label-1").fill("High");

    await page.getByTestId("dynamic-field-form-submit").click();
    await expect(page.getByTestId("dynamic-field-form")).not.toBeVisible();
    await expect(page.getByText("Priority Bucket", { exact: true })).toBeVisible();
  });

  test("blocks submission of a dropdown with no options", async ({ page }) => {
    await page.goto("/admin/dynamic-fields");
    await page.getByTestId("admin-new-button").click();
    await page.getByTestId("dynamic-field-name").fill("Empty Dropdown");
    await page.getByTestId("dynamic-field-label").fill("Empty dropdown");
    await page.getByTestId("dynamic-field-order").fill("3");
    await page.getByTestId("dynamic-field-type").selectOption("Dropdown");
    await page.getByTestId("dynamic-field-form-submit").click();
    await expect(page.getByTestId("dynamic-field-form")).toBeVisible();
    await expect(page.getByText("Add at least one option.")).toBeVisible();
  });
});
