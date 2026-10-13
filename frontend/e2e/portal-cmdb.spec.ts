import { test, expect } from "@playwright/test";
import { mockPortalApi, loginAsCustomer } from "./fixtures/mock-portal-api";

for (const vp of [
  { name: "1280", width: 1280, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const) {
  test.describe(`portal devices and systems at ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

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

    test("creates a ticket with the preselected system", async ({ page }) => {
      await page.goto("/portal/tickets/new?ci=7");
      await expect(page.getByTestId("portal-new-ticket-ci-7")).toBeChecked();
      await page.getByTestId("portal-new-ticket-subject").fill("Mailserver antwortet nicht");
      await page.getByTestId("portal-new-ticket-body").fill("Seit heute Morgen keine Verbindung zu mail.example.org.");
      const request = page.waitForRequest((r) => r.url().endsWith("/api/portal/tickets") && r.method() === "POST");
      await page.getByTestId("portal-new-ticket-submit").click();
      const body = (await request).postDataJSON() as { config_item_ids?: number[] };
      expect(body.config_item_ids).toEqual([7]);
      await page.waitForURL(/\/portal\/tickets\/300/);
    });
  });
}
