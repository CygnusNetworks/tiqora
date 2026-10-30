import { test, expect } from "@playwright/test";
import { mockPortalApi, loginAsCustomer } from "./fixtures/mock-portal-api";

for (const vp of [
  { name: "1280", width: 1280, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const) {
  test.describe(`portal change schedule at ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test.beforeEach(async ({ page }) => {
      await mockPortalApi(page);
      await loginAsCustomer(page);
    });

    test("lists the maintenance windows with their services and does not scroll sideways", async ({ page }) => {
      await page.getByTestId("portal-nav-changes").click();
      await page.waitForURL(/\/portal\/change-schedule$/);
      const card = page.getByTestId("portal-change-20261012130");
      await expect(card).toContainText("Wartung der Mail-Gateways");
      await expect(card).toContainText("E-Mail");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  });
}
