import { test, expect } from "@playwright/test";
import { mockPortalApi, loginAsCustomer } from "./fixtures/mock-portal-api";

test.describe("portal devices and systems", () => {
  test.beforeEach(async ({ page }) => {
    await mockPortalApi(page);
    await loginAsCustomer(page);
  });

  test("lists a device, opens it and preselects it in the ticket form", async ({ page }) => {
    await page.getByTestId("portal-nav-cmdb").click();
    await page.waitForURL(/\/portal\/config-items$/);
    await page.getByTestId("portal-ci-7").click();
    await expect(page.getByTestId("ci-view-hostname")).toContainText("mail.example.org");
    await page.getByTestId("portal-ci-create-ticket").click();
    await page.waitForURL(/\/portal\/tickets\/new\?ci=7/);
    await expect(page.getByTestId("portal-new-ticket-ci-7")).toBeChecked();
  });
});
