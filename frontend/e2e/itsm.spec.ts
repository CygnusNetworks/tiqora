import { test, expect, type Page } from "@playwright/test";
import { DEMO_ITSM_MODE_KEY, type DemoItsmMode } from "../src/demo/itsmMode";
import { mockApi, loginAsAgent } from "./fixtures/mock-api";

/**
 * ITSM core in demo mode (no backend): the ticket zoom flows (affected CIs, note to linked tickets,
 * problem management, major incident) and the admin ITSM page. The write routes are answered by the
 * stateful ITSM store of `fixtures/itsm-store.ts`; the scenario is described there.
 */

/** The mock reads the mode from the page's localStorage (default "tiqora" = Mode B). */
async function useItsmMode(page: Page, mode: DemoItsmMode) {
  await page.addInitScript(
    ({ key, value }) => {
      try {
        localStorage.setItem(key, value);
      } catch {
        /* no storage: the default mode */
      }
    },
    { key: DEMO_ITSM_MODE_KEY, value: mode },
  );
}

test.describe("ITSM ticket flows", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
  });

  test("incident: link a CI with the picker, see the service state, remove the CI", async ({ page }) => {
    await useItsmMode(page, "znuny_readthrough");
    await page.goto("/agent/tickets/100");

    const panel = page.getByTestId("ticket-cis");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("ticket-ci-201")).toContainText("srv-web-01.example.org");
    await expect(page.getByTestId("ticket-ci-203")).toHaveCount(0);

    // The header chips carry the service and its live incident state.
    await expect(page.getByTestId("itsm-chip-service-state")).toContainText("Kundenportal");
    await expect(page.getByTestId("itsm-chip-service-state")).toContainText("Störung");

    await page.getByTestId("ticket-cis-add").click();
    await page.getByTestId("ci-picker-search").fill("Mailserver");
    const hit = page.getByTestId("ci-picker-hit-203");
    await expect(hit).toBeVisible();
    await expect(page.getByTestId("ci-picker-hit-201")).toHaveCount(0);
    await hit.click();

    await expect(page.getByTestId("ci-picker")).toHaveCount(0);
    const linked = page.getByTestId("ticket-ci-203");
    await expect(linked).toContainText("Mailserver Postfix");
    await expect(linked).toContainText("Betriebsbereit");
    // Mode A: the edit link leads into Znuny.
    await expect(page.getByTestId("ticket-ci-edit-203")).toHaveAttribute("href", /znuny\.example\.org/);

    await page.getByTestId("ticket-ci-remove-203").click();
    await expect(page.getByTestId("ticket-ci-203")).toHaveCount(0);
    await expect(page.getByTestId("ticket-ci-201")).toBeVisible();
  });

  test("linked tickets: note to all of them runs as a job and lists the result", async ({ page }) => {
    await page.goto("/agent/tickets/100");
    await expect(page.getByTestId("linked-ticket-103")).toBeVisible();
    await expect(page.getByTestId("linked-ticket-104")).toBeVisible();

    await page.getByTestId("linked-note-open").click();
    const dialog = page.getByTestId("linked-note-dialog");
    await expect(dialog).toBeVisible();
    // All linked tickets are preselected.
    await expect(page.getByTestId("linked-note-target-103")).toBeChecked();
    await expect(page.getByTestId("linked-note-target-104")).toBeChecked();
    await expect(page.getByTestId("linked-note-submit")).toBeDisabled();

    await page.getByTestId("linked-note-subject").fill("Störung Kundenportal");
    await page.getByTestId("linked-note-body").fill("Die Ursache ist bekannt, Update folgt um 10 Uhr.");
    await page.getByTestId("linked-note-submit").click();

    const result = page.getByTestId("linked-note-result");
    await expect(result).toBeVisible();
    await expect(page.getByTestId("linked-note-done")).toContainText("20261012003");
    // One target is skipped with its reason; the other ticket is not listed as done.
    await expect(page.getByTestId("linked-note-skipped")).toContainText("20261012004");
    await expect(page.getByTestId("linked-note-skipped")).toContainText("no write access");
    await expect(page.getByTestId("linked-note-done")).not.toContainText("20261012004");

    await page.getByTestId("linked-note-close").click();
    await expect(dialog).toHaveCount(0);
  });

  test("problem: link two incidents, publish the known error, close the incidents with the workaround", async ({
    page,
  }) => {
    await page.goto("/agent/tickets/101");
    await expect(page.getByTestId("problem-panel")).toBeVisible();
    await expect(page.getByTestId("problem-workaround")).toHaveValue("Server auf Port 12 umgesteckt");
    await expect(page.getByTestId("problem-incidents-empty")).toBeVisible();
    await expect(page.getByTestId("problem-close-incidents")).toHaveCount(0);

    await page.getByTestId("problem-link-incidents").click();
    await page.getByTestId("problem-incident-search").fill("20261012");
    await page.getByTestId("problem-incident-hit-102").check();
    await page.getByTestId("problem-incident-hit-105").check();
    // The problem itself is never offered as its own incident.
    await expect(page.getByTestId("problem-incident-hit-101")).toHaveCount(0);
    await expect(page.getByTestId("problem-incident-submit")).toContainText("2");
    await page.getByTestId("problem-incident-submit").click();

    await expect(page.getByTestId("problem-incident-picker")).toHaveCount(0);
    await expect(page.getByTestId("problem-incident-102")).toContainText("Portal antwortet langsam");
    await expect(page.getByTestId("problem-incident-105")).toContainText("Login-Seite lädt nicht");

    await expect(page.getByTestId("problem-known-error-badge")).toHaveCount(0);
    await page.getByTestId("problem-known-error").click();
    await expect(page.getByTestId("problem-known-error-badge")).toBeVisible();
    await expect(page.getByTestId("problem-article-state")).toContainText("Published");

    await page.getByTestId("problem-close-incidents").click();
    await expect(page.getByTestId("problem-close-confirm")).toContainText("2 open incident");
    await page.getByTestId("problem-close-submit").click();
    await expect(page.getByTestId("problem-close-result")).toContainText("Closed 2 ticket(s)");
    await page.getByTestId("problem-close-finish").click();

    // Nothing is left to close: the action disappears and the incidents show as closed.
    await expect(page.getByTestId("problem-close-incidents")).toHaveCount(0);
    await expect(page.getByTestId("problem-incident-102")).toContainText("closed");
  });

  test("major incident: declare, banner on a linked ticket, internal update in the timeline", async ({ page }) => {
    await page.goto("/agent/tickets/100");
    await expect(page.getByTestId("major-none")).toBeVisible();
    await expect(page.getByTestId("major-update-form")).toHaveCount(0);

    await page.getByTestId("major-declare").click();
    await page.getByTestId("major-declare-summary").fill("Kundenportale nicht erreichbar");
    await page.getByTestId("major-declare-submit").click();

    await expect(page.getByTestId("major-summary")).toContainText("Kundenportale nicht erreichbar");
    await expect(page.getByTestId("major-timeline-declared")).toBeVisible();
    await expect(page.getByTestId("itsm-chip-role")).toContainText("Major");

    // Internal is the default delivery: nothing leaves the system.
    await expect(page.getByTestId("major-update-mode")).toHaveValue("internal");
    await page.getByTestId("major-update-body").fill("Ursache eingegrenzt, Switch wird getauscht.");
    await page.getByTestId("major-update-submit").click();
    const update = page.getByTestId("major-timeline-update");
    await expect(update).toBeVisible();
    await expect(update).toContainText("Internal only");
    await expect(update.frameLocator("iframe").locator("body")).toContainText("Switch wird getauscht");

    // A ticket linked to the major incident shows the banner.
    await page.goto("/agent/tickets/103");
    const banner = page.getByTestId("major-banner");
    await expect(banner).toBeVisible();
    await expect(page.getByTestId("major-banner-link")).toHaveText("20261012000");
    await expect(page.getByTestId("major-banner-summary")).toContainText("Kundenportale nicht erreichbar");
  });
});

test.describe("ITSM admin", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("Tiqora CMDB mode: preview the setup, apply it and edit the priority matrix", async ({ page }) => {
    await loginAsAgent(page);
    await page.goto("/admin/itsm");
    await expect(page.getByTestId("admin-itsm-page")).toBeVisible();
    await expect(page.getByTestId("itsm-mode-current")).toBeVisible();

    // Preview: the Change group is not set up yet.
    await expect(page.getByTestId("itsm-setup-status-ticket_type-Incident")).toContainText("present");
    const change = page.getByTestId("itsm-setup-status-ticket_type-Change");
    await expect(change).toContainText("missing");
    await expect(page.getByTestId("itsm-setup-status-dynamic_field-ITSMCriticality")).toContainText("missing");

    await page.getByTestId("itsm-setup-check-change").check();
    await page.getByTestId("itsm-setup-apply").click();
    await expect(change).toContainText("present");
    await expect(page.getByTestId("itsm-setup-status-dynamic_field-ITSMCriticality")).toContainText("present");
    await expect(page.getByTestId("itsm-setup-status-dynamic_field-ITSMDecisionResult")).toContainText("present");

    // Matrix: editable in Mode B; one cell changes and survives a reload.
    const matrix = page.getByTestId("priority-matrix");
    await expect(matrix).toHaveAttribute("data-editable", "true");
    const cell = page.getByTestId("matrix-cell-0-0");
    await expect(cell).toContainText("1 very low");
    await expect(page.getByTestId("priority-matrix-save")).toBeDisabled();
    await cell.click();
    await page.getByTestId("matrix-cell-0-0-menu-option-4").click({ force: true });
    await expect(cell).toContainText("2 low");
    await page.getByTestId("priority-matrix-save").click();
    await expect(page.getByTestId("priority-matrix-save")).toBeDisabled();

    await page.reload();
    await expect(page.getByTestId("matrix-cell-0-0")).toContainText("2 low");
    await expect(page.getByTestId("matrix-cell-1-1")).toContainText("3 normal");
  });

  test("Znuny read-through mode: the matrix is read-only", async ({ page }) => {
    await useItsmMode(page, "znuny_readthrough");
    await loginAsAgent(page);
    await page.goto("/admin/itsm");
    await expect(page.getByTestId("admin-itsm-page")).toBeVisible();
    await expect(page.getByTestId("itsm-mode-a-note")).toBeVisible();

    const matrix = page.getByTestId("priority-matrix");
    await expect(matrix).toHaveAttribute("data-editable", "false");
    await expect(page.getByTestId("priority-matrix-readonly")).toBeVisible();
    await expect(page.getByTestId("priority-matrix-save")).toHaveCount(0);
    // Plain names, no selects: Znuny's own cip_allocate.
    await expect(page.getByTestId("matrix-cell-0-0")).toHaveText("1 very low");
  });
});
