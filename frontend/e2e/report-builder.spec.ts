import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent, useGermanUi } from "./fixtures/mock-api";

test.describe("report builder", () => {
  test.beforeEach(async ({ page }) => {
    await useGermanUi(page);
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("build, save and reopen a report", async ({ page }) => {
    await page.goto("/agent/reports");
    await page.getByTestId("reports-new").click();
    await page.getByTestId("builder-source").click();
    await page.getByRole("option", { name: "Tickets", exact: true }).click();
    await page.getByTestId("builder-rows-add").click();
    await page.getByRole("menuitem", { name: "Kanal", exact: true }).click();
    await page.getByTestId("builder-values-add").click();
    await page.getByRole("menuitem", { name: "Tickets", exact: true }).click();
    await expect(page.getByTestId("builder-preview-view")).toBeVisible();
    await expect(page.getByTestId("builder-preview-view")).toContainText("Telefon42");
    await page.getByTestId("builder-name").fill("Tickets je Kanal");
    await page.getByTestId("builder-save").click();
    await page.getByTestId("save-report-dialog-confirm").click();
    await expect(page).toHaveURL(/\/agent\/reports\/\d+$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Tickets je Kanal");
    await page.goto("/agent/reports");
    await expect(page.getByTestId("reports-saved-mine")).toContainText("Tickets je Kanal");
  });

  test("copy a built-in into the builder", async ({ page }) => {
    await page.goto("/agent/reports");
    await page.getByTestId("reports-card-builtin.volume-copy").click();
    await expect(page.getByTestId("builder-name")).toHaveValue("Kopie von Erstellte und geschlossene Tickets");
  });
});
