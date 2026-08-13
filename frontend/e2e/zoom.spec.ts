import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

test.describe("ticket zoom", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("shows sanitized article body, external banner, and attachments", async ({
    page,
  }) => {
    await page.goto("/agent/tickets/100");
    await expect(page.getByTestId("ticket-zoom")).toBeVisible();
    await expect(page.getByTestId("ticket-header")).toContainText("Printer");

    // Email ticket → split (master/detail) view by default: open the first
    // article from the list into the reader pane.
    await page.getByTestId("article-list-item-500").click();

    await expect(page.getByTestId("article-body-html")).toBeVisible();
    await expect(page.getByTestId("article-body-iframe")).toBeVisible();
    await expect(page.getByTestId("external-images-banner")).toBeVisible();
    await expect(page.getByTestId("attachment-900")).toContainText(
      "screenshot.png",
    );

    // History moved into the ticket-zoom ⋯ overflow menu. The menu is a
    // portal with an entrance animation, so the item can still be detached
    // when Playwright's stability check runs — force-click it.
    await page.getByTestId("ticket-zoom-overflow-trigger").click();
    await page.getByTestId("overflow-tab-history").click({ force: true });
    await expect(page.getByTestId("history-table")).toBeVisible();
  });

  test("reply on a foreign-locked ticket shows the takeover banner (Znuny RequiredLock)", async ({
    page,
  }) => {
    // Ticket 101 is mocked as locked by "Bea Blocker" until takeover.
    await page.goto("/agent/tickets/101");
    await expect(page.getByTestId("ticket-zoom")).toBeVisible();
    await page.getByTestId("ticket-actions-reply").click();

    const banner = page.getByTestId("composer-lock-banner");
    await expect(banner).toContainText("Bea Blocker");
    await expect(page.getByTestId("reply-send")).toBeDisabled();

    await page.getByTestId("composer-lock-takeover").click();
    await expect(banner).toBeHidden();
  });
});
