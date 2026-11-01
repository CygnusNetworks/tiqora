import { test, expect, type Page } from "@playwright/test";
import { mockApi, loginAsAgent, useGermanUi } from "./fixtures/mock-api";
import { mockAdminApi } from "./fixtures/mock-admin-api";
import { mockPortalApi, loginAsCustomer } from "./fixtures/mock-portal-api";
import { approvalsStore } from "./fixtures/approvals-store";
import { DEMO_APPROVALS_OFF_KEY } from "../src/demo/moduleSwitches";

/**
 * Approvals (shared approval engine) in demo mode, no backend: the agent inbox and the ticket panel,
 * the public confirm page, the customer portal and the admin template editor. The fixtures answer from
 * the stateful `fixtures/approvals-store.ts`, so a decision sticks for the rest of a test. German copy.
 */

// Custom selects render as portal listboxes: open the trigger, then force-click the option (the entrance
// animation makes the portal briefly fail Playwright's stability check).
async function pick(page: Page, testId: string, value: string) {
  await page.getByTestId(testId).click();
  await page.getByTestId(`${testId}-menu-option-${value}`).click({ force: true });
}

test.describe("agent inbox and ticket panel", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
    await loginAsAgent(page);
  });

  test("approving from the inbox decrements the badge and the ticket panel shows Freigegeben", async ({ page }) => {
    await page.goto("/agent/approvals");
    await expect(page.getByTestId("approvals-page")).toBeVisible();
    await expect(page.getByTestId("agent-nav-approvals")).toContainText("2");
    await expect(page.getByTestId("approval-row")).toHaveCount(2);
    // The overdue, escalated request comes first.
    await expect(page.getByTestId("approval-row").first()).toHaveAttribute("data-approval-id", "503");
    await expect(page.getByTestId("approval-row-overdue")).toBeVisible();

    await page.locator('[data-testid="approval-row"][data-approval-id="503"]').click();
    await expect(page.getByTestId("approval-detail-title")).toContainText("Firewall-Regel freigeben");
    await page.getByTestId("approval-approve").click();
    await page.getByTestId("decision-comment").fill("Regel ist unkritisch.");
    await page.getByTestId("decision-submit").click();

    await expect(page.getByTestId("approval-row")).toHaveCount(1);
    await expect(page.getByTestId("agent-nav-approvals")).toContainText("1");
    await expect(page.getByTestId("agent-nav-approvals")).not.toContainText("2");
    await expect(page.getByTestId("approval-detail").getByTestId("approval-status-badge").first()).toContainText("Freigegeben");
    await expect(page.getByTestId("approval-actions")).toHaveCount(0);

    // Same decision on the ticket the request hangs on.
    await page.goto("/agent/tickets/131");
    const item = page.getByTestId("ticket-approvals-panel").getByTestId("ticket-approval");
    await expect(item).toHaveCount(1);
    await expect(item.getByTestId("ticket-approval-title")).toContainText("Firewall-Regel freigeben");
    await expect(item.getByTestId("approval-status-badge")).toContainText("Freigegeben");
    await expect(item.getByTestId("ticket-approval-approve")).toHaveCount(0);

    // And under "Erledigt".
    await page.goto("/agent/approvals?tab=done");
    await expect(page.getByTestId("approval-row")).toHaveCount(2);
    await expect(page.locator('[data-testid="approval-row"][data-approval-id="503"]')).toContainText("Freigegeben");
  });

  test("a rejection needs a reason and ends the request", async ({ page }) => {
    await page.goto("/agent/approvals");
    await page.locator('[data-testid="approval-row"][data-approval-id="501"]').click();
    await expect(page.getByTestId("approval-steps")).toBeVisible();
    await page.getByTestId("approval-reject").click();
    await page.getByTestId("decision-submit").click();
    await expect(page.getByTestId("decision-comment-error")).toContainText("Begründung");
    await page.getByTestId("decision-comment").fill("Rückfallplan fehlt noch.");
    await page.getByTestId("decision-submit").click();

    await expect(page.getByTestId("approval-detail").getByTestId("approval-status-badge").first()).toContainText("Abgelehnt");
    await expect(page.getByTestId("approval-row")).toHaveCount(1);
    await expect(page.getByTestId("agent-nav-approvals")).toContainText("1");
  });

  test("a two-stage request moves on to the next stage after the vote that completes the first", async ({ page }) => {
    await page.goto("/agent/approvals?id=501");
    // Stage 2 has not started: the engine has not resolved its approvers yet, so no count and no list.
    const later = page.getByTestId("approval-step").nth(1);
    await expect(later).toContainText("Betriebsleitung");
    await expect(later.getByTestId("approval-step-starts-after")).toContainText("Startet nach Stufe 1");
    await expect(later.getByTestId("approval-step-approvers-later")).toBeVisible();
    await expect(later.getByTestId("approval-approver")).toHaveCount(0);
    await expect(later).not.toContainText("0 Freigaben");
    await expect(page.getByTestId("approval-approver-source").first()).toContainText("über Freigabegruppe CAB Netzwerk");

    await page.getByTestId("approval-approve").click();
    await page.getByTestId("decision-submit").click();
    // Majority of three reached (Erika Muster voted before): the Betriebsleitung stage is active, nothing left for us.
    await expect(page.getByTestId("approval-actions")).toHaveCount(0);
    await expect(page.getByTestId("approval-step").nth(1)).toContainText("Betriebsleitung");
    await expect(page.getByTestId("approval-step").nth(1).getByTestId("approval-approver-source")).toContainText("über Gruppe Betriebsleitung");
    await expect(page.getByTestId("approval-detail").getByTestId("approval-status-badge").first()).toContainText("Offen");
  });

  test("withdrawing from the inbox asks first", async ({ page }) => {
    await page.goto("/agent/approvals?id=503");
    await page.getByTestId("approval-cancel").click();
    await expect(page.getByText("Anforderung zurückziehen?")).toBeVisible();
    await page.getByTestId("confirm-dialog-cancel").click();
    await expect(page.getByTestId("approval-detail").getByTestId("approval-status-badge").first()).toContainText("Offen");
    await page.getByTestId("approval-cancel").click();
    await page.getByTestId("confirm-dialog-confirm").click();
    await expect(page.getByTestId("approval-detail").getByTestId("approval-status-badge").first()).toContainText("Zurückgezogen");
  });

  test("requests an ad-hoc approval from a template on the ticket", async ({ page }) => {
    await page.goto("/agent/tickets/100");
    const panel = page.getByTestId("ticket-approvals-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("ticket-approval")).toHaveCount(1);

    await panel.getByTestId("ticket-approvals-request").click();
    await page.getByTestId("request-submit").click();
    await expect(page.getByTestId("request-problem")).toBeVisible();
    await page.getByTestId("request-title").fill("Zugang zum Backup-Server freigeben");
    await pick(page, "request-template", "standard_einzel");
    await page.getByTestId("request-submit").click();

    await expect(panel.getByTestId("ticket-approval")).toHaveCount(2);
    const created = panel.getByTestId("ticket-approval").first();
    await expect(created.getByTestId("ticket-approval-title")).toContainText("Backup-Server");
    await expect(created.getByTestId("approval-status-badge")).toContainText("Offen");
    await expect(created.getByTestId("ticket-approval-cancel")).toBeVisible();

    // Withdrawing moves it under "Ältere Freigaben".
    await created.getByTestId("ticket-approval-cancel").click();
    await expect(page.getByText("Anforderung zurückziehen?")).toBeVisible();
    await page.getByTestId("confirm-dialog-confirm").click();
    await expect(panel.getByTestId("ticket-approvals-older-toggle")).toBeVisible();
    await expect(panel.getByTestId("ticket-approval")).toHaveCount(1);
  });

  test("requests an approval with own approvers, but never offers approve on timeout", async ({ page }) => {
    await page.goto("/agent/tickets/100");
    await page.getByTestId("ticket-approvals-request").click();
    await page.getByTestId("request-mode-custom").click();
    await page.getByTestId("request-title").fill("Beschaffung freigeben");
    await page.getByTestId("definition-add-step").click();
    await page.getByTestId("step-0-name").fill("Teamleitung");
    await page.getByTestId("step-0-approvers-add").click();
    await pick(page, "step-0-approvers-ref-0", "2");

    // F9: "Freigeben bei Zeitablauf" is for admin templates and consumer definitions only.
    await page.getByTestId("step-0-timeout").click();
    await expect(page.getByTestId("step-0-timeout-menu-option-reject")).toBeVisible();
    await expect(page.getByTestId("step-0-timeout-menu-option-approve")).toHaveCount(0);
    await page.getByTestId("step-0-timeout-menu-option-reject").click({ force: true });

    await page.getByTestId("request-submit").click();
    await expect(page.getByTestId("ticket-approvals-panel").getByTestId("ticket-approval").first()).toContainText("Beschaffung freigeben");
  });

  test("substitute tab: new substitute, a past end date, and a substitution that ended meanwhile", async ({ page }) => {
    await page.goto("/agent/approvals?tab=delegation");
    await expect(page.getByTestId("delegation-row")).toHaveCount(1);
    await expect(page.getByTestId("delegation-substitute")).toContainText("Erika Muster");

    // A past end date is refused before anything is sent.
    await pick(page, "delegation-substitute-select", "3");
    await page.getByTestId("delegation-until").fill("2020-01-01T10:00");
    await expect(page.getByTestId("delegation-period-error")).toContainText("Zukunft");
    await page.getByTestId("delegation-until").fill("2030-01-01T10:00");
    await expect(page.getByTestId("delegation-period-error")).toHaveCount(0);
    await page.getByTestId("delegation-create").click();
    await expect(page.getByTestId("delegation-row")).toHaveCount(2);
    await expect(page.getByTestId("delegation-substitute").nth(1)).toContainText("Max Beispiel");

    // Ending one that is already over (403 delegation_not_active) is reported as such, not as an error.
    approvalsStore().endDelegationElsewhere();
    await page.getByTestId("delegation-revoke").first().click();
    await expect(page.getByTestId("delegation-ended")).toContainText("Bereits beendet");
    await expect(page.getByTestId("delegation-row-error")).toHaveCount(0);
  });
});

test.describe("agent inbox on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the detail opens as a sheet and a decision closes the open request", async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
    await loginAsAgent(page);
    await page.goto("/agent/approvals");
    await expect(page.getByTestId("approval-row")).toHaveCount(2);
    await expect(page.getByTestId("approval-panel")).toHaveCount(0);

    await page.locator('[data-testid="approval-row"][data-approval-id="503"]').click();
    const sheet = page.getByTestId("approval-sheet");
    await expect(sheet).toBeVisible();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(sheet.getByTestId("approval-detail-title")).toContainText("Firewall-Regel freigeben");
    const box = await sheet.boundingBox();
    expect(box?.width).toBeLessThanOrEqual(390);

    await sheet.getByTestId("approval-approve").click();
    await page.getByTestId("decision-submit").click();
    await expect(sheet.getByTestId("approval-detail").getByTestId("approval-status-badge").first()).toContainText("Freigegeben");

    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
    await expect(page.getByTestId("approval-row")).toHaveCount(1);
    // The page's own content fits the phone (the shell header's account menu overflows on every page, not ours).
    const content = await page.getByTestId("approvals-page").boundingBox();
    expect(content?.width).toBeLessThanOrEqual(390);
  });

  test("the sheet is a real dialog: focus moves in, Escape closes it and focus returns to the row", async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
    await loginAsAgent(page);
    await page.goto("/agent/approvals");
    const row = page.locator('[data-testid="approval-row"][data-approval-id="501"]');
    await row.focus();
    await row.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect.poll(() => page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
    // Tab stays inside the sheet however often it is pressed.
    for (let i = 0; i < 12; i++) await page.keyboard.press("Tab");
    expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(row).toBeFocused();
  });
});

test.describe("public confirm page", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await useGermanUi(page);
  });

  test("approve with the token in the fragment, which is removed from the address bar", async ({ page }) => {
    await page.goto("/approve#t=demo");
    await expect(page.getByTestId("approval-link-request-title")).toContainText("Change freigeben");
    expect(page.url()).toMatch(/\/approve$/);
    await expect(page.getByTestId("approval-link-approver")).toContainText("Erika Muster");
    await expect(page.getByTestId("approval-link-step")).toContainText("CAB");
    await expect(page.getByTestId("approval-link-rule")).toBeVisible();
    await page.getByTestId("approval-link-approve").click();
    await expect(page.getByTestId("approval-link-done")).toContainText("Freigabe wurde gespeichert");
    await expect(page.getByTestId("approval-link-status")).toContainText("Freigegeben");
  });

  test("reject needs a reason", async ({ page }) => {
    await page.goto("/approve#t=demo");
    await page.getByTestId("approval-link-reject").click();
    await expect(page.getByTestId("approval-link-comment-error")).toContainText("Begründung");
    await page.getByTestId("approval-link-comment").fill("Wartungsfenster passt nicht.");
    await page.getByTestId("approval-link-reject").click();
    await expect(page.getByTestId("approval-link-done")).toContainText("Ablehnung wurde gespeichert");
    await expect(page.getByTestId("approval-link-status")).toContainText("Abgelehnt");
  });

  test("a link that was used already says so, on open and on a second decide", async ({ page, context }) => {
    // Two tabs open the same e-mail link; the second one decides first.
    await page.goto("/approve#t=demo");
    await expect(page.getByTestId("approval-link-approve")).toBeVisible();
    const other = await context.newPage();
    await mockApi(other);
    await other.goto("/approve#t=demo");
    await other.getByTestId("approval-link-approve").click();
    await expect(other.getByTestId("approval-link-done")).toBeVisible();

    await page.getByTestId("approval-link-approve").click();
    await expect(page.getByTestId("approval-link-error")).toContainText("bereits entschieden");
    await expect(page.getByTestId("approval-link-retry")).toHaveCount(0);

    // Opening it again is answered the same way.
    await other.goto("about:blank"); // a hash-only change of /approve would not reload the page
    await other.goto("/approve#t=demo");
    await expect(other.getByTestId("approval-link-error")).toContainText("bereits entschieden");
  });

  test("an unknown token and a link without token", async ({ page }) => {
    await page.goto("/approve#t=nope");
    await expect(page.getByTestId("approval-link-error")).toContainText("nicht mehr gültig");
    await page.goto("/approve");
    await expect(page.getByTestId("approval-link-error")).toContainText("unvollständig");
  });
});

test.describe("customer portal", () => {
  test("the customer approves an open request", async ({ page }) => {
    await mockPortalApi(page);
    await useGermanUi(page);
    await loginAsCustomer(page);
    await page.goto("/portal/approvals");
    await expect(page.getByTestId("portal-approvals-list")).toBeVisible();
    await page.getByTestId("portal-approval-601").click();
    await expect(page.getByTestId("portal-approval-page")).toContainText("Neuen Zugang für den Dienstleister freigeben");
    await expect(page.getByTestId("portal-approval-lines")).toContainText("kunde1@example.org");

    await page.getByTestId("portal-approval-approve").click();
    await expect(page.getByTestId("portal-approval-done")).toBeVisible();
    await expect(page.getByTestId("portal-approval-mine")).toContainText("Freigegeben");
    await expect(page.getByTestId("portal-approval-form")).toHaveCount(0);

    // Gone from "Offen", listed under "Erledigt".
    await page.getByTestId("portal-approval-back").click();
    await expect(page.getByTestId("portal-approvals-empty")).toBeVisible();
    await page.getByRole("tab", { name: "Erledigt" }).click();
    await expect(page.getByTestId("portal-approval-601")).toBeVisible();
  });

  test("a rejection needs a reason", async ({ page }) => {
    await mockPortalApi(page);
    await useGermanUi(page);
    await loginAsCustomer(page);
    await page.goto("/portal/approvals/601");
    await page.getByTestId("portal-approval-reject").click();
    await expect(page.getByTestId("portal-approval-comment-error")).toContainText("Begründung");
    await page.getByTestId("portal-approval-comment").fill("Nicht von uns beauftragt.");
    await page.getByTestId("portal-approval-reject").click();
    await expect(page.getByTestId("portal-approval-mine")).toContainText("Abgelehnt");
  });
});

test.describe("admin templates", () => {
  test("creates a template with two stages and a Freigabegruppe", async ({ page }) => {
    await mockAdminApi(page);
    await useGermanUi(page);
    await loginAsAgent(page);
    await page.goto("/admin/approvals/templates");
    await expect(page.getByTestId("template-row")).toHaveCount(2);

    await page.getByTestId("template-new").click();
    await page.getByTestId("template-key").fill("cab_notfall");
    await page.getByTestId("template-name").fill("CAB Notfall");
    await page.getByTestId("template-subject-ticket").check();

    // Stage 1: the Freigabegruppe "CAB Netzwerk" decides by majority.
    await page.getByTestId("step-0-name").fill("CAB");
    await page.getByTestId("step-0-rule-majority").click();
    await page.getByTestId("step-0-approvers-add").click();
    await pick(page, "step-0-approvers-kind-0", "list");
    await pick(page, "step-0-approvers-ref-0", "1");
    // Admin templates may approve automatically on timeout (consumer carry from Task 20).
    await page.getByTestId("step-0-timeout").click();
    const auto = page.getByTestId("step-0-timeout-menu-option-approve");
    await expect(auto).toBeVisible();
    await expect(auto).toBeEnabled();
    await auto.click({ force: true });

    // Stage 2: the ticket owner, after the CAB.
    await page.getByTestId("definition-add-step").click();
    await expect(page.getByTestId("step-1-stage-label")).toContainText("Stufe 2");
    await page.getByTestId("step-1-name").fill("Betriebsleitung");
    await page.getByTestId("step-1-approvers-add").click();
    await pick(page, "step-1-approvers-kind-0", "resolver");
    await pick(page, "step-1-approvers-ref-0", "ticket.owner");

    await expect(page.getByTestId("template-check-ok")).toBeVisible();
    const posted = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/api/v1/admin/approvals/templates"));
    await page.getByTestId("template-save").click();
    const body = (await posted).postDataJSON() as {
      template_key: string;
      subject_types: string[];
      definition: { steps: { name: string; stage: number; rule: string; on_timeout: string; approvers: { kind: string; ref: string }[] }[] };
    };
    expect(body.template_key).toBe("cab_notfall");
    expect(body.subject_types).toEqual(["ticket"]);
    expect(body.definition.steps.map((s) => s.stage)).toEqual([1, 2]);
    expect(body.definition.steps[0]).toMatchObject({ name: "CAB", rule: "majority", on_timeout: "approve" });
    expect(body.definition.steps[0].approvers).toEqual([{ kind: "list", ref: "1" }]);
    expect(body.definition.steps[1].approvers).toEqual([{ kind: "resolver", ref: "ticket.owner" }]);

    await expect(page.getByTestId("template-dialog")).toHaveCount(0);
    await expect(page.getByTestId("template-row")).toHaveCount(3);
    await expect(page.locator('[data-testid="template-row"][data-template-key="cab_notfall"]')).toContainText("CAB Notfall");
  });
});

test.describe("switched off (approvals.enabled = false)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript((key) => localStorage.setItem(key, "1"), DEMO_APPROVALS_OFF_KEY);
    await useGermanUi(page);
  });

  test("the agent sees no entry and no panel, a deep link says it is off", async ({ page }) => {
    await mockApi(page);
    await loginAsAgent(page);
    await page.goto("/agent/tickets/131");
    await expect(page.getByTestId("ticket-zoom-page").or(page.getByRole("main")).first()).toBeVisible();
    await expect(page.getByTestId("ticket-approvals-panel")).toHaveCount(0);
    await expect(page.getByTestId("agent-nav-approvals")).toHaveCount(0);
    await page.goto("/agent/approvals");
    await expect(page.getByTestId("approvals-page")).toContainText("Freigaben sind derzeit ausgeschaltet");
  });

  test("the admin template editor still offers approval groups and dynamic sources", async ({ page }) => {
    await mockAdminApi(page);
    await loginAsAgent(page);
    await page.goto("/admin/approvals/templates");
    await expect(page.getByTestId("template-row")).toHaveCount(2);
    await page.getByTestId("template-new").click();
    await page.getByTestId("template-key").fill("cab_aus");
    await page.getByTestId("template-name").fill("CAB aus");
    await page.getByTestId("template-subject-ticket").check();
    await page.getByTestId("step-0-name").fill("CAB");
    await page.getByTestId("step-0-approvers-add").click();
    await pick(page, "step-0-approvers-kind-0", "list");
    await pick(page, "step-0-approvers-ref-0", "1");
    await pick(page, "step-0-approvers-kind-0", "resolver");
    await pick(page, "step-0-approvers-ref-0", "ticket.owner");
  });

  test("the public link says so in plain words", async ({ page }) => {
    await mockApi(page);
    await page.goto("/approve#t=demo");
    await expect(page.getByText("Freigaben sind derzeit ausgeschaltet.", { exact: false })).toBeVisible();
    await expect(page.getByTestId("approval-link-approve")).toHaveCount(0);
  });
});
