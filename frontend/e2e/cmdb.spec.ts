import { test, expect, type Page } from "@playwright/test";
import { mockApi, loginAsAgent, useGermanUi as germanUi } from "./fixtures/mock-api";

// CMDB in Mode B on the in-memory demo store (two classes, six CIs, one DependsOn chain with a CI
// in incident). Every flow runs at desktop and at phone width.
const VIEWPORTS = [
  { name: "1280", width: 1280, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

for (const vp of VIEWPORTS) {
  test.describe(`CMDB agent flows at ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test.beforeEach(async ({ page }) => {
      await germanUi(page);
      await mockApi(page);
      await loginAsAgent(page);
    });

    test("filters the list by class and free text and opens a CI", async ({ page }) => {
      await page.goto("/agent/cmdb");
      await expect(page.getByTestId("cmdb-list-page")).toBeVisible();
      const total = page.getByTestId("cmdb-total");
      await expect(total).toHaveText(/^6 CIs/);

      await page.getByTestId("ci-filter-class").click();
      await page.getByTestId("ci-filter-class-menu-option-2").click();
      await expect(total).toHaveText(/^2 CIs/);

      await page.getByTestId("ci-filter-class").click();
      await page.getByTestId("ci-filter-class-menu-option-1").click();
      await page.getByTestId("ci-filter-q").fill("web-01");
      await expect(total).toHaveText(/^1 CI/);
      const row = page.locator(`[data-testid="ci-row-1"], [data-testid="ci-card-1"]`).filter({ visible: true });
      await expect(row).toContainText("web-01");

      await row.getByRole("link", { name: "SRV-0001" }).click();
      await page.waitForURL(/\/agent\/cmdb\/1$/);
      await expect(page.getByTestId("ci-name")).toContainText("web-01");
      await expect(page.getByTestId("ci-number")).toContainText("SRV-0001");
    });

    test("shows versions and a diff, then edits a value and saves", async ({ page }) => {
      await page.goto("/agent/cmdb/1?tab=versions");
      await expect(page.getByTestId("ci-versions")).toBeVisible();
      // select two versions and compare them
      const selects = page.locator('[data-testid^="ci-version-select-"]');
      await expect(selects.nth(1)).toBeVisible();
      await selects.nth(0).check();
      await selects.nth(1).check();
      await page.getByTestId("ci-versions-compare").click();
      await expect(page.getByTestId("ci-diff")).toBeVisible();

      await page.getByTestId("ci-edit").click();
      await page.waitForURL(/\/agent\/cmdb\/1\/edit$/);
      const hostname = page.getByTestId("ci-attr-hostname-1-input");
      await expect(hostname).toHaveValue("web-01.example.org");
      await hostname.fill("web-01-neu.example.org");
      await page.getByTestId("ci-edit-note").fill("Umbenannt");
      await page.getByTestId("ci-edit-save").click();
      await page.waitForURL(/\/agent\/cmdb\/1(\?.*)?$/);
      await expect(page.getByTestId("cmdb-detail-page")).toContainText("web-01-neu.example.org");
    });

    test("adds a link and opens the dependency graph", async ({ page }) => {
      await page.goto("/agent/cmdb/1?tab=links");
      const before = await page.locator('[data-testid^="ci-link-"][data-testid$="-type"]').count();
      await page.getByTestId("ci-link-add").click();
      await expect(page.getByTestId("ci-link-dialog")).toBeVisible();
      await page.getByTestId("ci-link-target").fill("lb-01");
      await page.getByTestId("ci-link-target-result-4").click();
      await expect(page.getByTestId("ci-link-type")).toContainText("DependsOn");
      await page.getByTestId("ci-link-submit").click();
      await expect(page.getByTestId("ci-link-dialog")).toHaveCount(0);
      await expect.poll(() => page.locator('[data-testid^="ci-link-"][data-testid$="-type"]').count()).toBeGreaterThan(before);

      await page.goto("/agent/cmdb/1?tab=dependencies");
      await expect(page.getByTestId("ci-impact")).toBeVisible();
      await expect(page.locator('[data-testid^="impact-item-"], [data-testid^="impact-node-"]').first()).toBeAttached();
    });
  });
}

async function gotoAs(page: Page, path: string) {
  await germanUi(page);
  await mockApi(page);
  await loginAsAgent(page);
  // the group picker of the class dialogs reads the admin group list, which the agent mock lacks
  await page.route("**/api/v1/admin/groups?*", async (route) => {
    const items = [{ id: 1, name: "users", comments: "", valid_id: 1, change_time: "2026-10-08T09:30:00", create_time: "2026-10-08T09:30:00" }];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items, total: 1, page: 1, page_size: 500 }) });
  });
  await page.goto(path);
}

for (const vp of VIEWPORTS) {
  test.describe(`CMDB admin and import flows at ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test("creates a class from a template and saves its first definition", async ({ page }) => {
      await gotoAs(page, "/admin/cmdb/classes");
      await page.getByTestId("cmdb-from-template").click();
      await page.getByTestId("cmdb-template-network").click();
      await page.getByTestId("cmdb-template-group").click();
      await page.getByTestId(/^cmdb-template-group-menu-option-/).first().click();
      await page.getByTestId("cmdb-template-apply").click();
      await page.waitForURL(/\/admin\/cmdb\/classes\/\d+$/);

      await expect(page.getByTestId("definition-editor")).toBeVisible();
      await page.getByTestId("def-add").click();
      await page.getByTestId("def-key").fill("rack_unit");
      await page.getByTestId("def-name").fill("Höheneinheit");
      await page.getByTestId("def-save-version").click();
      await page.getByTestId("def-comment").fill("Erste Version");
      await page.getByTestId("def-version-confirm").click();
      await expect(page.getByTestId("def-version-error")).toHaveCount(0);
      // the demo applies a template as an empty class: its first saved definition is version 1
      await expect(page.getByTestId("def-version-1")).toBeVisible();
    });

    test("an attribute added to an existing class becomes a new definition version", async ({ page }) => {
      await gotoAs(page, "/admin/cmdb/classes/1");
      await expect(page.getByTestId("def-version-1")).toBeVisible();
      await page.getByTestId("def-add").click();
      await page.getByTestId("def-key").fill("rack_unit");
      await page.getByTestId("def-name").fill("Höheneinheit");
      await page.getByTestId("def-save-version").click();
      await page.getByTestId("def-comment").fill("Höheneinheit ergänzt");
      await page.getByTestId("def-version-confirm").click();
      await expect(page.getByTestId("def-version-2")).toContainText("Höheneinheit ergänzt");
      await expect(page.getByTestId("def-version-1")).toBeVisible();
    });

    test("import wizard dry run shows counts and blocks the real import on errors", async ({ page }) => {
      await gotoAs(page, "/agent/cmdb/import");
      await page.getByTestId("import-class").click();
      await page.getByTestId("import-class-menu-option-1").click();
      const csv = [
        "number;name;class;deployment_state;incident_state;attr:hostname;attr:ip",
        "SRV-0001;web-01;Server;production;operational;web-01.example.org;10.0.0.1",
        "SRV-0002;web-02;Server;production;operational;web-02.example.org;10.0.0.2",
      ].join("\n");
      await page.getByTestId("import-file").setInputFiles({ name: "export.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
      await expect(page.getByTestId("import-next-1")).toBeEnabled();
      await page.getByTestId("import-next-1").click();
      await expect(page.getByTestId("mapping-editor")).toBeVisible();
      await page.getByTestId("import-next-2").click();
      await expect(page.getByTestId("import-next-3")).toBeDisabled();
      await page.getByTestId("import-dry-start").click();
      // the demo store reports 3 new rows and 1 faulty one
      await expect(page.getByTestId("import-dry-count-created")).toHaveText(/3/);
      await expect(page.getByTestId("import-dry-count-errors")).toHaveText(/1/);
      await expect(page.getByTestId("import-dry-blocked")).toBeVisible();
    });
  });
}
