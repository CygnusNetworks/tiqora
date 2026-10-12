import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("GenericInterface debugger", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("filters the log, opens an entry, sends it again and sees the job finish", async ({ page }) => {
    await page.goto("/admin/webservices/1?tab=debugger");
    await expect(page.getByTestId("ws-tab-debugger")).toBeVisible();
    await expect(page.getByTestId("debug-row-tiqora-106")).toBeVisible();
    await expect(page.getByTestId("debug-row-znuny-105")).toContainText("Znuny");
    // Znuny rows cannot be sent again
    await expect(page.getByTestId("debug-resend-znuny-105")).toHaveCount(0);

    // Richtung: only incoming calls
    await page.getByTestId("debug-filter-direction-Provider").click();
    await expect(page.getByTestId("debug-row-tiqora-104")).toBeVisible();
    await expect(page.getByTestId("debug-row-tiqora-106")).toHaveCount(0);

    // Quelle: Znuny + all directions
    await page.getByTestId("debug-filter-direction-all").click();
    await page.getByTestId("debug-filter-source-znuny").click();
    await expect(page.getByTestId("debug-row-znuny-102")).toBeVisible();
    await expect(page.getByTestId("debug-row-tiqora-106")).toHaveCount(0);
    await page.getByTestId("debug-filter-source-all").click();

    // open the failed outgoing call
    const row = page.getByTestId("debug-row-tiqora-106");
    await row.click();
    await expect(page.getByTestId("debug-drawer")).toBeVisible();
    await expect(page.getByTestId("debug-line-content-1064")).toContainText("Response code '503'");
    await expect(page.getByTestId("debug-line-masked-1063")).toContainText("Masked by Tiqora");

    // Escape closes the drawer and focus returns to the row
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("debug-drawer")).toHaveCount(0);
    await expect(row).toBeFocused();

    // resend: confirm text, then the job runs to the end
    await row.click();
    await page.getByTestId("debug-resend").click();
    await expect(page.getByTestId("confirm-dialog")).toContainText(
      "The call is sent again with the current ticket data. Continue?",
    );
    await page.getByTestId("confirm-dialog-confirm").click();
    await expect(page.getByTestId("debug-resend-done")).toBeVisible();
    await expect(page.getByTestId("debug-resend-job-status")).toBeVisible();
    await page.getByTestId("debug-resend-show-log").click();
    await expect(page.getByTestId("debug-drawer")).toHaveCount(0);
  });
});
