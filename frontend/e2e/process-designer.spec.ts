import { test, expect, type Locator, type Page } from "@playwright/test";
import { loginAsAgent } from "./fixtures/mock-api";
import { mockAdminApi } from "./fixtures/mock-admin-api";

/** Drags a palette item onto the canvas at an offset inside it (HTML5 drag and drop). */
async function dropStep(page: Page, dx: number, dy: number) {
  await page.getByTestId("process-designer-palette-new").dragTo(page.getByTestId("process-designer-canvas"), {
    targetPosition: { x: dx, y: dy },
  });
}

/** React Flow draws connections with the mouse: from a source handle to a target handle. */
async function connect(page: Page, from: Locator, to: Locator) {
  const a = await from.locator(".react-flow__handle.source").boundingBox();
  const b = await to.locator(".react-flow__handle.target").boundingBox();
  if (!a || !b) throw new Error("handles not rendered");
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
  await page.mouse.up();
}

test.describe("process designer", () => {
  // Palette, canvas and panel sit side by side; the canvas needs room for two steps.
  test.use({ viewport: { width: 1600, height: 1000 } });

  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
    await loginAsAgent(page);
  });

  test("builds a process, checks it, deploys it and waits for Znuny", async ({ page }) => {
    // New process: the name comes from a prompt, then the designer opens on the empty draft.
    await page.goto("/admin/processes");
    await page.getByTestId("process-new").click();
    await page.getByTestId("confirm-dialog-input").fill("Order approval");
    await page.getByTestId("confirm-dialog-confirm").click();
    await expect(page.getByTestId("process-designer-canvas")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Order approval" })).toBeVisible();
    await expect(page.getByTestId("process-designer-node")).toHaveCount(0);

    // Two steps, dropped from the palette.
    await dropStep(page, 40, 60);
    await expect(page.getByTestId("process-designer-node")).toHaveCount(1);
    await dropStep(page, 380, 60);
    await expect(page.getByTestId("process-designer-node")).toHaveCount(2);

    // Connect them: a new transition with a name.
    const nodes = page.getByTestId("process-designer-node");
    await connect(page, nodes.nth(0), nodes.nth(1));
    await expect(page.getByTestId("process-designer-connect-popover")).toBeVisible();
    await page.getByPlaceholder(/transition/i).fill("Approved");
    await page.getByTestId("process-designer-connect-new").click();
    await expect(page.getByTestId("process-designer-edge-label")).toHaveText("Approved");
    // The new transition is selected; give it an action: set the state.
    await page.getByTestId("transition-actions-new").click();
    await expect(page.getByTestId("action-panel")).toBeVisible();
    await expect(page.getByTestId("action-panel-module")).toHaveValue("TicketStateSet");
    await page.getByTestId("action-field-State|StateID-input").fill("closed successful");
    await page.getByTestId("action-panel-save").click();
    await expect(page.getByTestId("action-panel-save")).toBeDisabled();

    // First step: a form with two fields.
    await nodes.nth(0).click();
    await expect(page.getByTestId("activity-panel")).toBeVisible();
    await page.getByTestId("activity-panel-new").click();
    await expect(page.getByTestId("activity-panel-forms").locator("li")).toHaveCount(1);
    await page.getByTestId("activity-panel-save").click();
    await expect(page.getByTestId("activity-panel-save")).toBeDisabled();
    await page.getByTestId("activity-panel-forms").locator("li button[data-testid^='activity-panel-open-']").click();
    await expect(page.getByTestId("dialog-panel")).toBeVisible();
    await page.getByTestId("dialog-panel-name").fill("Request");
    for (const field of ["Title", "Queue"]) {
      await page.getByTestId("dialog-panel-add").click();
      await page.getByTestId(`dialog-panel-add-menu-option-${field}`).click({ force: true });
      await expect(page.getByTestId(`dialog-panel-field-${field}`)).toBeVisible();
    }
    await expect(page.getByTestId("dialog-panel-fields").locator("> li")).toHaveCount(2);
    await page.getByTestId("dialog-panel-save").click();
    await expect(page.getByTestId("dialog-panel-save")).toBeDisabled();

    // Save the canvas, then check: no findings.
    await page.getByTestId("process-designer-save").click();
    await expect(page.getByTestId("process-designer-dirty")).toHaveCount(0);
    await page.getByTestId("process-designer-check").click();
    await expect(page.getByTestId("validation-none")).toBeVisible();

    // Deploy: Znuny has not applied it yet, then the mock's add-on reports the same revision.
    await expect(page.getByTestId("deploy-status-pending")).toBeVisible();
    await page.getByTestId("deploy-bar-deploy").click();
    await expect(page.getByTestId("deploy-sync-pending")).toBeVisible();
    await expect(page.getByTestId("deploy-sync-ok")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("deploy-status-pending")).toHaveCount(0);

    // Back to the list: the process is no longer a draft and can be exported.
    await page.getByRole("link", { name: /back/i }).click();
    await expect(page.getByTestId("admin-processes-page")).toBeVisible();
    const row = page.getByRole("row", { name: /Order approval/ });
    await expect(row).toBeVisible();
    await expect(row.locator("[data-testid^='process-draft-']")).toHaveCount(0);
    const exportLink = row.locator("[data-testid^='process-export-']");
    await expect(exportLink).toHaveAttribute("href", /\/api\/v1\/admin\/process-designer\/processes\/Process-\d+\/export$/);
    await expect(exportLink).toHaveAttribute("download", "");
  });

  test("a concurrent change shows the conflict banner and reloads the draft", async ({ page }) => {
    await page.goto("/admin/processes/Process-1");
    await expect(page.getByTestId("process-designer-canvas")).toBeVisible();
    await expect(page.getByTestId("process-designer-node")).toHaveCount(2);

    // Edit locally (move a step), then let somebody else save the process first.
    const node = page.getByTestId("process-designer-node").first();
    const box = await node.boundingBox();
    if (!box) throw new Error("node not rendered");
    await page.mouse.move(box.x + 30, box.y + 10);
    await page.mouse.down();
    await page.mouse.move(box.x + 30, box.y + 120, { steps: 8 });
    await page.mouse.up();
    await expect(page.getByTestId("process-designer-dirty")).toBeVisible();
    await page.evaluate(async () => {
      const base = "/api/v1/admin/process-designer/processes/Process-1";
      const cur = (await (await fetch(base)).json()) as { process: { version: string; config: Record<string, unknown> } };
      await fetch(base, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          expected_version: cur.process.version,
          name: "Onboarding (renamed elsewhere)",
          description: "",
          state_entity_id: "S1",
          path: cur.process.config.Path,
          layout: {},
          start_activity: "Activity-a",
          start_activity_dialog: "ActivityDialog-1",
        }),
      });
    });

    await page.getByTestId("process-designer-save").click();
    const banner = page.getByTestId("process-designer-conflict");
    await expect(banner).toBeVisible();
    await banner.getByRole("button").click();
    await expect(banner).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Onboarding (renamed elsewhere)" })).toBeVisible();
    await expect(page.getByTestId("process-designer-dirty")).toHaveCount(0);
  });
});
