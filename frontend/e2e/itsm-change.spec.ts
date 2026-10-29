import { test, expect, type Page } from "@playwright/test";
import { DEMO_ITSM_MODE_KEY, type DemoItsmMode } from "../src/demo/itsmMode";
import { mockApi, loginAsAgent, useGermanUi } from "./fixtures/mock-api";
import { changeStore } from "./fixtures/change-store";

/**
 * Change management (ITSM, Mode B) in demo mode, no backend. The changes a test creates live in the
 * stateful `fixtures/change-store.ts`, which answers with the backend's phase table and guard codes;
 * the CAB's vote and the closing of task tickets happen in other screens, so the tests stand in for
 * them with `decideApproval` / `finishTasks`. Fixtures (calendar, CAB meetings, Znuny archive, admin
 * pages) come from the demo resolver. German copy.
 */

// Custom selects render as portal listboxes: open the trigger, then force-click the option (the entrance
// animation makes the portal briefly fail Playwright's stability check).
async function pick(page: Page, testId: string, value: string | number) {
  await page.getByTestId(testId).click();
  await page.getByTestId(`${testId}-menu-option-${value}`).click({ force: true });
}

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

/** The step a person takes on the change page, up to the dialog's submit button. */
async function step(page: Page, to: string) {
  await page.getByTestId(`change-action-${to}`).click();
  await page.getByTestId("change-transition-submit").click();
}

const failureCodes = (page: Page) => page.getByTestId("change-guard-failure");

test.describe("normal change from request to closure", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
    await loginAsAgent(page);
  });

  test("template, tasks job, risk, approval, implementation, review and close", async ({ page }) => {
    // --- create from the "Server-Wartung" template (two tasks, plans and CAB from its defaults)
    await page.goto("/agent/itsm/changes");
    await page.getByTestId("change-list-create").click();
    await page.getByTestId("change-create-type-normal").click();
    await pick(page, "change-create-template", 2);
    await page.getByTestId("change-create-title").fill("Mail-Server Wartung im November");
    await pick(page, "change-create-service", 1);
    await page.getByTestId("change-create-start").fill("2026-11-10T20:00");
    await page.getByTestId("change-create-end").fill("2026-11-11T00:00");
    await page.getByTestId("change-create-submit").click();

    // the tasks job finishes, the dialog hands over to the new change
    await expect(page).toHaveURL(/\/agent\/itsm\/changes\/300$/);
    const record = page.getByTestId("change-record");
    await expect(record).toHaveAttribute("data-phase", "draft");
    await expect(page.getByTestId("change-detail-header")).toContainText("Mail-Server Wartung im November");
    await expect(page.getByTestId("change-task")).toHaveCount(2);
    await expect(page.getByTestId("change-tasks-progress")).toContainText("0");
    await expect(page.getByTestId("change-plan-implementation_plan")).toContainText("Wartungsmeldung versenden");

    // --- submit, start the assessment
    await step(page, "submitted");
    await expect(record).toHaveAttribute("data-phase", "submitted");
    await step(page, "assessment");
    await expect(record).toHaveAttribute("data-phase", "assessment");

    // --- the CAB step is refused until the questionnaire is answered and the impact is computed
    await page.getByTestId("change-action-approval").click();
    await page.getByTestId("change-transition-submit").click();
    await expect(failureCodes(page)).toHaveCount(2);
    await expect(page.locator('[data-testid="change-guard-failure"][data-code="questionnaire_incomplete"]')).toBeVisible();
    await expect(page.locator('[data-testid="change-guard-failure"][data-code="impact_pending"]')).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("change-transition-dialog")).toHaveCount(0);

    await pick(page, "change-risk-answer-tested", "yes");
    await pick(page, "change-risk-answer-downtime", "short");
    await page.getByTestId("change-risk-answers-save").click();
    await expect(page.getByTestId("change-risk-questionnaire-open")).toHaveCount(0);
    await page.getByTestId("change-risk-evaluate").click();
    await expect(page.getByTestId("change-risk-summary")).toContainText("3 Punkte");

    await expect(page.getByTestId("change-impact-stale")).toBeVisible();
    await page.getByTestId("change-impact-recompute").click();
    await expect(page.getByTestId("change-impact-stale")).toHaveCount(0);
    await expect(page.getByTestId("change-impact-service")).toHaveCount(1);

    // --- request the approval: the CAB round 1 is pending
    await step(page, "approval");
    await expect(record).toHaveAttribute("data-phase", "approval");
    const snapshot = page.getByTestId("change-approval-snapshot");
    await expect(snapshot).toHaveAttribute("data-status", "pending");
    await expect(snapshot).toContainText("Runde 1");
    // while the CAB decides, window and plans are frozen
    await page.getByTestId("change-plans-edit").click();
    await expect(page.getByTestId("change-plans-locked")).toBeVisible();
    await expect(page.getByTestId("change-plans-input-reason")).toBeVisible();
    await expect(page.getByTestId("change-plans-input-implementation_plan")).toHaveCount(0);

    // --- the CAB approves (outcome job), the change is scheduled
    changeStore().decideApproval(300, "approved");
    await page.reload();
    await expect(record).toHaveAttribute("data-phase", "scheduled");
    await expect(page.getByTestId("change-approval-snapshot")).toHaveAttribute("data-status", "approved");

    // --- start; the review step is refused while tasks are open
    await step(page, "implementing");
    await expect(record).toHaveAttribute("data-phase", "implementing");
    await page.getByTestId("change-action-review").click();
    await page.getByTestId("change-transition-submit").click();
    await expect(page.locator('[data-testid="change-guard-failure"][data-code="tasks_open"]')).toBeVisible();

    // --- the task tickets are closed in the zoom; the same dialog goes through, with the review notes
    changeStore().finishTasks(300);
    await page.getByTestId("change-pir-notes").fill("Die Wartung verlief ohne Unterbrechung; der Rückfallplan war nicht nötig.");
    await page.getByTestId("change-transition-submit").click();
    await expect(record).toHaveAttribute("data-phase", "review");
    await expect(page.getByTestId("change-plan-pir_notes")).toContainText("ohne Unterbrechung");
    await expect(page.getByTestId("change-tasks-progress")).toContainText("2");

    // --- close with an outcome; the notes are already stored
    await page.getByTestId("change-action-closed").click();
    await expect(page.getByTestId("change-pir-notes")).toHaveValue(/ohne Unterbrechung/);
    await pick(page, "change-pir-outcome", "successful");
    await page.getByTestId("change-transition-submit").click();
    await expect(record).toHaveAttribute("data-phase", "closed");
    await expect(page.getByTestId("change-phase-closed")).toHaveAttribute("data-state", "current");
    await expect(page.getByTestId("change-actions")).toHaveCount(0);
    await expect(page.getByTestId("change-incident-offer")).toHaveCount(0);

    // the history holds every step
    const events = page.getByTestId("change-event");
    await expect(events.first()).toContainText("Phase");
    expect(await events.count()).toBeGreaterThanOrEqual(10);
    await expect(page.getByTestId("change-events")).toContainText("Freigabe angefordert");
    await expect(page.getByTestId("change-events")).toContainText("Ergebnis der Freigabe");

    // and the new change is in the list, closed
    await page.goto("/agent/itsm/changes");
    const row = page.getByTestId("change-row-300");
    await expect(row).toContainText("Mail-Server Wartung im November");
    await expect(row.getByTestId("change-phase-badge")).toContainText("Abgeschlossen");
  });

  test("a rejected change is reworked and requested again as round 2", async ({ page }) => {
    await page.goto("/agent/itsm/changes");
    await page.getByTestId("change-list-create").click();
    await pick(page, "change-create-template", 2);
    await page.getByTestId("change-create-title").fill("Switch-Austausch Etage 3");
    await page.getByTestId("change-create-start").fill("2026-11-14T20:00");
    await page.getByTestId("change-create-end").fill("2026-11-14T23:00");
    await page.getByTestId("change-create-submit").click();
    await expect(page).toHaveURL(/\/agent\/itsm\/changes\/300$/);

    const record = page.getByTestId("change-record");
    await step(page, "submitted");
    await step(page, "assessment");
    await pick(page, "change-risk-answer-tested", "yes");
    await pick(page, "change-risk-answer-downtime", "none");
    await page.getByTestId("change-risk-answers-save").click();
    await expect(page.getByTestId("change-risk-questionnaire-open")).toHaveCount(0);
    await page.getByTestId("change-impact-recompute").click();
    await expect(page.getByTestId("change-impact-stale")).toHaveCount(0);
    await step(page, "approval");
    await expect(record).toHaveAttribute("data-phase", "approval");

    changeStore().decideApproval(300, "rejected");
    await page.reload();
    await expect(record).toHaveAttribute("data-phase", "rejected");
    await expect(page.getByTestId("change-approval-snapshot")).toHaveAttribute("data-status", "rejected");

    // "Überarbeiten": back to the draft; the next request is a new round
    await step(page, "draft");
    await expect(record).toHaveAttribute("data-phase", "draft");
    await step(page, "submitted");
    await step(page, "assessment");
    await step(page, "approval");
    await expect(record).toHaveAttribute("data-phase", "approval");
    await expect(page.getByTestId("change-approval-snapshot")).toContainText("Runde 2");
  });
});

test.describe("emergency change in a block freeze window", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
    await loginAsAgent(page);
  });

  async function createEmergency(page: Page) {
    await page.goto("/agent/itsm/changes");
    await page.getByTestId("change-list-create").click();
    await page.getByTestId("change-create-type-emergency").click();
    await page.getByTestId("change-create-title").fill("Firewall-Regel gegen laufenden Angriff");
    // inside the "Jahresabschluss" freeze (block, no emergency exception)
    await page.getByTestId("change-create-start").fill("2026-12-30T20:00");
    await page.getByTestId("change-create-end").fill("2026-12-30T22:00");
    await page.getByTestId("change-create-submit").click();
    await expect(page).toHaveURL(/\/agent\/itsm\/changes\/300$/);
  }

  test("the conflict is shown and a manager overrides it with a reason", async ({ page }) => {
    await createEmergency(page);
    const record = page.getByTestId("change-record");
    await expect(record).toHaveAttribute("data-phase", "draft");
    await expect(page.getByTestId("change-phase-bar")).toHaveAttribute("data-change-type", "emergency");

    // the freeze shows as a blocking conflict; the risk check blocks as well
    const conflict = page.getByTestId("change-conflict");
    await expect(conflict).toHaveCount(1);
    await expect(conflict).toHaveAttribute("data-kind", "freeze");
    await expect(conflict).toHaveAttribute("data-severity", "block");
    await page.getByTestId("change-risk-evaluate").click();
    await expect(page.getByTestId("change-risk-blocked")).toBeVisible();

    // first try: plans and the freeze are listed together; the manager is offered the override
    await page.getByTestId("change-action-approval").click();
    await page.getByTestId("change-transition-submit").click();
    await expect(page.locator('[data-testid="change-guard-failure"][data-code="plans_missing"]')).toBeVisible();
    await expect(page.locator('[data-testid="change-guard-failure"][data-code="block_conflict"]')).toBeVisible();
    await expect(page.getByTestId("change-transition-override-reason")).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByTestId("change-plans-edit").click();
    await page.getByTestId("change-plans-input-implementation_plan").fill("Regel auf beiden Firewalls anlegen und aktivieren.");
    await page.getByTestId("change-plans-input-backout_plan").fill("Regel wieder entfernen.");
    await page.getByTestId("change-plans-save").click();
    await expect(page.getByTestId("change-plan-backout_plan")).toContainText("Regel wieder entfernen");

    // second try: only the freeze is left; without a reason it stays refused
    await page.getByTestId("change-action-approval").click();
    await page.getByTestId("change-transition-submit").click();
    await expect(failureCodes(page)).toHaveCount(1);
    await expect(failureCodes(page)).toHaveAttribute("data-code", "block_conflict");
    await page.getByTestId("change-transition-submit").click();
    await expect(failureCodes(page)).toHaveCount(1);

    // third try: with the reason the CAB request goes out and the history names it
    await page.getByTestId("change-transition-override-reason").fill("Aktiver Angriff, Schutz geht dem Sperrzeitraum vor.");
    await page.getByTestId("change-transition-submit").click();
    await expect(record).toHaveAttribute("data-phase", "approval");
    await expect(page.getByTestId("change-approval-snapshot")).toHaveAttribute("data-status", "pending");
    await expect(page.getByTestId("change-events")).toContainText("Sperrzeitraum übergangen");
    await expect(page.getByTestId("change-events")).toContainText("Aktiver Angriff");
  });

  test("without the manager role the override is not offered", async ({ page }) => {
    changeStore().manager = false;
    await createEmergency(page);
    await page.getByTestId("change-plans-edit").click();
    await page.getByTestId("change-plans-input-implementation_plan").fill("Regel anlegen.");
    await page.getByTestId("change-plans-input-backout_plan").fill("Regel entfernen.");
    await page.getByTestId("change-plans-save").click();
    await expect(page.getByTestId("change-plan-backout_plan")).toContainText("Regel entfernen");

    await page.getByTestId("change-action-approval").click();
    await page.getByTestId("change-transition-submit").click();
    await expect(failureCodes(page)).toHaveCount(1);
    await expect(failureCodes(page)).toHaveAttribute("data-code", "block_conflict");
    await expect(page.getByTestId("change-transition-override")).toHaveCount(0);
    await expect(page.getByTestId("change-record")).toHaveAttribute("data-phase", "draft");
  });

});

test.describe("closing the implementation", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
    await loginAsAgent(page);
  });

  test("a manager forces the review past open tasks with a reason", async ({ page }) => {
    await page.goto("/agent/itsm/changes");
    await page.getByTestId("change-list-create").click();
    await pick(page, "change-create-template", 2);
    await page.getByTestId("change-create-title").fill("Server-Wartung Dezember");
    await page.getByTestId("change-create-start").fill("2026-11-20T20:00");
    await page.getByTestId("change-create-end").fill("2026-11-20T23:00");
    await page.getByTestId("change-create-submit").click();
    await expect(page).toHaveURL(/\/agent\/itsm\/changes\/300$/);

    await step(page, "submitted");
    await step(page, "assessment");
    await pick(page, "change-risk-answer-tested", "yes");
    await pick(page, "change-risk-answer-downtime", "none");
    await page.getByTestId("change-risk-answers-save").click();
    await expect(page.getByTestId("change-risk-questionnaire-open")).toHaveCount(0);
    await page.getByTestId("change-impact-recompute").click();
    await expect(page.getByTestId("change-impact-stale")).toHaveCount(0);
    await step(page, "approval");
    await expect(page.getByTestId("change-record")).toHaveAttribute("data-phase", "approval");
    changeStore().decideApproval(300, "approved");
    await page.reload();
    await step(page, "implementing");

    await page.getByTestId("change-action-review").click();
    await page.getByTestId("change-transition-submit").click();
    await expect(page.locator('[data-testid="change-guard-failure"][data-code="tasks_open"]')).toBeVisible();
    await page.getByTestId("change-transition-force").check();
    // forcing needs a reason: the submit stays disabled until it is given
    await expect(page.getByTestId("change-transition-reason-required")).toBeVisible();
    await expect(page.getByTestId("change-transition-submit")).toBeDisabled();
    await page.getByTestId("change-transition-reason").fill("Aufgaben wurden außerhalb des Systems erledigt.");
    await page.getByTestId("change-transition-submit").click();
    await expect(page.getByTestId("change-record")).toHaveAttribute("data-phase", "review");
    await expect(page.getByTestId("change-events")).toContainText("Schritt erzwungen");
  });
});

test.describe("change calendar", () => {
  test("month view shades the freeze windows, the service tab shows the projected availability", async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
    await page.clock.setFixedTime(new Date("2026-10-12T08:00:00Z"));
    await loginAsAgent(page);
    await page.goto("/agent/itsm/changes/calendar");
    await expect(page.getByTestId("changeCalendarPage")).toBeVisible();
    await expect(page.getByTestId("change-calendar-label")).toContainText("Oktober 2026");

    // block freeze (13.-15.10.) and warning freeze (24./25.10.)
    await expect(page.getByTestId("change-freeze-day-2026-10-14")).toHaveAttribute("data-enforcement", "block");
    await expect(page.getByTestId("change-freeze-day-2026-10-24")).toHaveAttribute("data-enforcement", "warn");
    await expect(page.getByTestId("change-freeze-day-2026-10-20")).toHaveCount(0);
    // the change of the open CAB request sits in the grid and leads to its page
    await expect(page.getByTestId("change-calendar-legend")).toBeVisible();
    await expect(page.getByText("Switch-Firmware im Rechenzentrum aktualisieren").first()).toBeVisible();

    await page.getByRole("tab", { name: "Woche" }).click();
    await expect(page.getByTestId("change-calendar-week")).toBeVisible();

    await page.getByRole("tab", { name: "Nach Service" }).click();
    await expect(page.getByTestId("change-calendar-services")).toBeVisible();
    await expect(page.getByTestId("change-service-row-1")).toContainText("E-Mail");
    await expect(page.getByTestId("change-service-row-2")).toContainText("VPN");

    // one cell per day of the range
    await expect(page.locator('[data-testid^="change-service-cell-1-"]').first()).toBeVisible();
  });
});

test.describe("Mode A: Znuny changes", () => {
  test("the sidebar leads to the read-only list and detail", async ({ page }) => {
    await useItsmMode(page, "znuny_readthrough");
    await mockApi(page);
    await useGermanUi(page);
    await loginAsAgent(page);
    await page.goto("/agent");

    const nav = page.getByTestId("agent-nav-changes");
    await expect(nav).toContainText("Changes (Znuny)");
    await nav.click();
    await expect(page).toHaveURL(/\/agent\/itsm\/legacy-changes$/);
    await expect(page.getByTestId("legacyChangeListPage")).toBeVisible();
    const rows = page.locator('[data-testid^="legacy-row-"]');
    await expect(rows.first()).toBeVisible();

    await rows.first().getByRole("link").click();
    await expect(page).toHaveURL(/\/agent\/itsm\/legacy-changes\/\d+$/);
    await expect(page.getByTestId("legacyChangeDetailPage")).toBeVisible();
    await expect(page.getByTestId("legacy-detail-header")).toBeVisible();
    await expect(page.getByTestId("legacy-detail-znuny-link")).toBeVisible();
    await expect(page.getByTestId("legacy-detail-workorders")).toBeVisible();

    // the Tiqora change management is not available: its page points back to the Znuny list
    await page.goto("/agent/itsm/changes/130");
    await expect(page.getByTestId("change-detail-error")).toBeVisible();
    await expect(page.getByTestId("change-detail-legacy")).toBeVisible();
  });
});

test.describe("change administration", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
    await loginAsAgent(page);
  });

  test("template editor saves a normal template with tasks", async ({ page }) => {
    await page.goto("/admin/itsm/change");
    await expect(page.getByTestId("admin-change-page")).toBeVisible();
    await expect(page.getByTestId("template-row")).toHaveCount(2);
    await expect(page.getByTestId("template-standard").first()).toBeVisible();

    await page.getByTestId("template-new").click();
    await expect(page.getByTestId("change-template-editor")).toBeVisible();
    // an empty name is not saved
    await page.getByTestId("template-save").click();
    await expect(page.getByTestId("template-problems")).toBeVisible();

    await page.getByTestId("template-name").fill("Zertifikat erneuern");
    await page.getByTestId("template-description").fill("Jährliche Erneuerung der Webserver-Zertifikate.");
    await page.getByTestId("template-task-add").click();
    await page.getByTestId("template-task-key-0").fill("renew");
    await page.getByTestId("template-task-title-0").fill("Zertifikat austauschen");
    await page.getByTestId("template-save").click();

    await expect.poll(() => changeStore().adminWrites.filter((w) => w.method === "POST" && w.path.endsWith("/templates")).length).toBe(1);
    const sent = changeStore().adminWrites.find((w) => w.path.endsWith("/templates"))?.body as {
      name: string;
      change_type: string;
      tasks: { key: string; title: string }[];
    };
    expect(sent.name).toBe("Zertifikat erneuern");
    expect(sent.change_type).toBe("normal");
    expect(sent.tasks).toHaveLength(1);
    expect(sent.tasks[0]).toMatchObject({ key: "renew", title: "Zertifikat austauschen" });
    // saved: back to the list
    await expect(page.getByTestId("admin-change-page")).toBeVisible();
  });

  test("freeze window with a weekly recurrence", async ({ page }) => {
    await page.goto("/admin/itsm/change?tab=freeze");
    await expect(page.getByTestId("change-freeze-tab")).toBeVisible();
    await expect(page.getByTestId("freeze-row").first()).toBeVisible();

    await page.getByTestId("freeze-new").click();
    await expect(page.getByTestId("freeze-dialog")).toBeVisible();
    // nothing entered: the problems are listed
    await page.getByTestId("freeze-save").click();
    await expect(page.getByTestId("freeze-problems")).toBeVisible();

    await page.getByTestId("freeze-name").fill("Wochenend-Wartungsruhe");
    await page.getByTestId("freeze-start").fill("2026-11-07T00:00");
    await page.getByTestId("freeze-end").fill("2026-11-09T00:00");
    await page.getByTestId("freeze-enforcement-warn").click();
    await pick(page, "freeze-recurrence-type", "Weekly");
    await page.getByTestId("freeze-recurrence-interval").fill("1");
    await page.getByTestId("freeze-recurrence-end-count").click();
    await page.getByTestId("freeze-recurrence-count").fill("10");
    await page.getByTestId("freeze-save").click();

    await expect(page.getByTestId("freeze-dialog")).toHaveCount(0);
    const write = changeStore().adminWrites.find((w) => w.method === "POST" && w.path.endsWith("/freeze-windows"));
    expect(write).toBeDefined();
    expect(write?.body).toMatchObject({
      name: "Wochenend-Wartungsruhe",
      enforcement: "warn",
      scope: "all",
      starts_at: "2026-11-07T00:00:00Z",
      ends_at: "2026-11-09T00:00:00Z",
      recurrence: { type: "Weekly", interval: 1, count: 10, until: null },
    });
  });
});

test.describe("CAB meeting", () => {
  test("the agenda lists the changes of the CAB and the suggestions", async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
    await page.clock.setFixedTime(new Date("2026-10-12T08:00:00Z"));
    await loginAsAgent(page);
    await page.goto("/agent/itsm/cab-meetings");
    await expect(page.getByTestId("cabMeetingsPage")).toBeVisible();
    await page.getByTestId("cab-meeting-1").getByRole("link").click();
    await expect(page.getByTestId("cabMeetingPage")).toBeVisible();
    await expect(page.getByTestId("cab-agenda-list")).toBeVisible();
    await expect(page.getByTestId("cab-agenda-item-130")).toBeVisible();
    // a change the agent may not read is only a position
    await expect(page.getByTestId("cab-agenda-hidden")).toBeVisible();
  });
});
