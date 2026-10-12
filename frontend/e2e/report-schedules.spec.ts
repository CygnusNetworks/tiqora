import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent, useGermanUi } from "./fixtures/mock-api";

test.describe("report schedules, PDF export and Znuny import", () => {
  test.beforeEach(async ({ page }) => {
    await useGermanUi(page);
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("import a Znuny report statistic and open the saved report", async ({ page }) => {
    await page.goto("/agent/reports");
    await page.getByTestId("reports-znuny-import").click();
    await expect(page.getByTestId("znuny-import-dialog")).toBeVisible();
    await page.getByRole("checkbox", { name: /Lösungszeit der letzten Woche/ }).check();
    await page.getByTestId("znuny-import-apply").click();
    const result = page.getByTestId("znuny-import-result");
    await expect(result).toContainText("Lösungszeit der letzten Woche");
    await result.getByRole("link", { name: "Lösungszeit der letzten Woche" }).click();
    await expect(page).toHaveURL(/\/agent\/reports\/105$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Lösungszeit der letzten Woche");
  });

  test("export a report as PDF", async ({ page }) => {
    await page.goto("/agent/reports/builtin.volume");
    const pdf = page.getByTestId("report-export-pdf");
    await expect(pdf).toBeEnabled();
    await pdf.click();
    const link = page.getByTestId("job-dialog-file-1");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", /\/api\/v1\/jobs\/9201\/files\/1$/);
  });

  test("create a schedule and send a test mail", async ({ page }) => {
    await page.goto("/agent/reports/builtin.volume");
    await page.getByTestId("report-view-schedule").click();
    await expect(page).toHaveURL(/\/agent\/reports\/schedules\/new/);
    await page.getByTestId("schedule-name").fill("Montag und Freitag");
    // weekly at 07:00 is the default; Monday is on, add Friday
    await page.getByTestId("recurrence-weekday-5").click();
    await expect(page.getByTestId("recurrence-weekday-1")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("recurrence-weekday-5")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("recurrence-time")).toHaveValue("07:00");
    // external recipients are on for this agent: an address becomes a chip
    const email = page.getByTestId("recipients-email-input");
    await email.fill("extern@example.org");
    await email.press("Enter");
    await expect(page.getByTestId("recipients-emails")).toContainText("extern@example.org");
    await page.getByTestId("schedule-save").click();

    await expect(page).toHaveURL(/\/agent\/reports\/schedules\/\d+$/);
    await expect(page.getByTestId("schedule-name")).toHaveValue("Montag und Freitag");
    await expect(page.getByTestId("recurrence-next-runs")).toBeVisible();
    await expect(page.getByText("Nächste Termine")).toBeVisible();
    await expect(page.getByTestId("schedule-runs-empty")).toBeVisible();

    await page.getByTestId("schedule-run-now").click();
    await page.getByTestId("schedule-run-test").click();
    const status = page.locator('[data-testid^="schedule-run-"][data-testid$="-status"]');
    await expect(status).toHaveText("Läuft");
    // the list is asked again every 5 s; the mock flips the run on the next poll
    await expect(status).toHaveText("Versendet", { timeout: 20_000 });
  });

  test("pause a schedule from the list", async ({ page }) => {
    await page.goto("/agent/reports/schedules");
    const row = page.getByTestId("schedule-row-1");
    await expect(row).not.toContainText("Angehalten");
    await page.getByTestId("schedule-toggle-1").click({ force: true });
    await expect(row).toContainText("Angehalten");
  });
});
