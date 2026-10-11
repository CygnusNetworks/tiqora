import { test, expect } from "@playwright/test";
import { mockApi, loginAsAgent, useGermanUi as germanUi } from "./fixtures/mock-api";

// The dashboards store of the mock is keyed by test id (R-DASH-E2E-STORE): pages of one test share
// it, tests never do. Default viewport 1440 wide so the grid stays at `lg` with the gallery open
// (the gallery is a side panel from 1280 px window width, an overlay below: R-GALLERY-1280).
test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await germanUi(page);
  await mockApi(page);
  await loginAsAgent(page);
});

test("view mode shows tabs, KPI counters with sparkline and no handles", async ({ page }) => {
  await page.goto("/agent");
  await expect(page.getByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
  await expect(page.getByTestId("dashboard-grid")).toHaveAttribute("data-breakpoint", "lg");
  const counters = page.locator('[data-testid^="widget-"][data-type="core.counter"]');
  await expect(counters).toHaveCount(4);
  await expect(counters.first().locator("svg[aria-hidden='true']").first()).toBeVisible();
  await expect(page.locator("[data-testid$='-handle']")).toHaveCount(0);
});

test("add a widget from the gallery by click and by drag, then save", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "Anpassen" }).click();
  await page.getByRole("button", { name: /^Kalender/ }).click();
  await expect(page.locator('[data-type="core.calendar-agenda"]')).toHaveCount(1);
  await page.getByRole("button", { name: /^Notizen/ }).dragTo(page.getByTestId("dashboard-grid"), {
    targetPosition: { x: 600, y: 300 },
  });
  await expect(page.locator('[data-type="core.notes"]')).toHaveCount(1);
  await page.getByRole("button", { name: "Speichern" }).click();
  await expect(page.getByRole("toolbar", { name: "Bearbeiten" })).toHaveCount(0);
  await page.reload();
  await expect(page.locator('[data-type="core.calendar-agenda"]')).toHaveCount(1);
  await expect(page.locator('[data-type="core.notes"]')).toHaveCount(1);
});

test("move by mouse, resize by keyboard, undo", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "Anpassen" }).click();
  const w = page.getByTestId("widget-demo-w-list-new");
  await expect(w).toBeVisible();
  const row = await w.evaluate((el) => getComputedStyle(el).gridRowStart);
  const cols = await w.evaluate((el) => getComputedStyle(el).gridColumnEnd);
  await w.getByTestId("widget-demo-w-list-new-handle").dragTo(page.getByTestId("dashboard-grid"), {
    targetPosition: { x: 200, y: 60 },
  });
  await expect.poll(() => w.evaluate((el) => getComputedStyle(el).gridRowStart)).not.toBe(row);
  await page.getByRole("button", { name: "Rückgängig" }).click();
  await expect.poll(() => w.evaluate((el) => getComputedStyle(el).gridRowStart)).toBe(row);
  await w.focus();
  await page.keyboard.press("Shift+ArrowRight");
  await expect.poll(() => w.evaluate((el) => getComputedStyle(el).gridColumnEnd)).not.toBe(cols);
});

test("resize by mouse", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "Anpassen" }).click();
  const w = page.getByTestId("widget-demo-w-list-new");
  const span = () => w.evaluate((el) => getComputedStyle(el).gridColumnEnd);
  const before = await span();
  const grip = page.getByTestId("widget-demo-w-list-new-resize");
  await grip.scrollIntoViewIfNeeded();
  const box = (await grip.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 150, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect.poll(span).not.toBe(before);
});

test("settings drawer changes columns and shows the live preview", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "Anpassen" }).click();
  await page.getByTestId("widget-demo-w-list-new").getByRole("button", { name: "Einstellungen" }).click();
  const dlg = page.getByRole("dialog", { name: "Einstellungen" });
  await expect(dlg.getByTestId("settings-preview").getByRole("columnheader", { name: "Kunde" })).toBeVisible();
  await dlg.getByRole("checkbox", { name: "Kunde" }).uncheck();
  await expect(dlg.getByTestId("settings-preview").getByRole("columnheader", { name: "Kunde" })).toHaveCount(0);
  await dlg.getByRole("button", { name: "Übernehmen" }).click();
  await expect(page.getByTestId("widget-demo-w-list-new").getByRole("columnheader", { name: "Kunde" })).toHaveCount(0);
});

test("draft survives a reload and can be restored; Verwerfen clears it", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "Anpassen" }).click();
  await page.getByTestId("widget-demo-w-list-new").getByRole("button", { name: "Entfernen" }).click();
  await expect(page.getByText(/Entwurf gesichert/)).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Anpassen" }).click();
  await page.getByRole("button", { name: /Entwurf von .* wiederherstellen/ }).click();
  await expect(page.getByTestId("widget-demo-w-list-new")).toHaveCount(0);
  await page.getByRole("button", { name: "Verwerfen" }).click();
  await page.getByRole("button", { name: "Änderungen verwerfen" }).click();
  await page.getByRole("button", { name: "Anpassen" }).click();
  await expect(page.getByRole("button", { name: /wiederherstellen/ })).toHaveCount(0);
});

test("two tabs: saving in the second after the first saved shows the conflict dialog and keeps the edit", async ({
  browser,
}) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const a = await ctx.newPage();
  const b = await ctx.newPage();
  for (const p of [a, b]) {
    await germanUi(p);
    await mockApi(p);
    await loginAsAgent(p);
    await p.goto("/agent");
    await p.getByRole("button", { name: "Anpassen" }).click();
  }
  await a.getByTestId("widget-demo-w-list-new").getByRole("button", { name: "Entfernen" }).click();
  await a.getByRole("button", { name: "Speichern" }).click();
  await expect(a.getByRole("toolbar", { name: "Bearbeiten" })).toHaveCount(0);
  await b.getByTestId("widget-demo-w-list-open").getByRole("button", { name: "Entfernen" }).click();
  await b.getByRole("button", { name: "Speichern" }).click();
  await expect(b.getByText(/inzwischen geändert/).first()).toBeVisible();
  await expect(b.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible();
  await ctx.close();
});

test("a second dashboard via '+' and switching tabs", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "Neues Dashboard" }).click();
  await page.getByLabel("Name").fill("Mein Tag");
  await page.getByRole("button", { name: "Anlegen" }).click();
  await expect(page.getByRole("tab", { name: "Mein Tag", selected: true })).toBeVisible();
  await page.getByRole("tab", { name: "Mein Start" }).click();
  await expect(page.getByTestId("dashboard-grid").locator("[data-type='core.counter']")).toHaveCount(4);
});

test("phone: single column, edit offers only up/down and settings", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/agent");
  await expect(page.getByTestId("dashboard-grid")).toHaveAttribute("data-breakpoint", "sm");
  await page.getByRole("button", { name: "Anpassen" }).click();
  const first = page.locator("[data-type='core.escalations']");
  await expect(first.getByRole("button", { name: "Nach oben" })).toBeVisible();
  await expect(first.getByRole("button", { name: "Einstellungen" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Duplizieren" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Widget hinzufügen" })).toHaveCount(0);
  const before = (await first.boundingBox())!;
  await first.getByRole("button", { name: "Nach oben" }).click();
  await expect.poll(async () => (await first.boundingBox())!.y).toBeLessThan(before.y);
});

test("a widget of an unknown type shows the unavailable tile and survives a save", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("tab", { name: "Alt" }).click();
  await expect(page.getByText("Widget nicht verfügbar")).toBeVisible();
  await page.getByRole("button", { name: "Anpassen" }).click();
  await page.getByRole("button", { name: "Speichern" }).click();
  await expect(page.getByRole("toolbar", { name: "Bearbeiten" })).toHaveCount(0);
  await page.reload();
  await page.getByRole("tab", { name: "Alt" }).click();
  await expect(page.getByText("Widget nicht verfügbar")).toBeVisible();
});

test("an unusable saved search offers 'Suche wählen' in view mode and opens the settings drawer", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("tab", { name: "Alt" }).click();
  await expect(page.getByRole("toolbar", { name: "Bearbeiten" })).toHaveCount(0);
  // Reachable without edit mode (R-DASH-SOURCE-ACTION): it switches to edit mode and opens the drawer.
  await page.getByRole("button", { name: "Suche wählen" }).click();
  await expect(page.getByRole("dialog", { name: "Einstellungen" })).toBeVisible();
  await expect(page.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible();
});

// Visual regression is deliberately NOT a toHaveScreenshot baseline (R-DASH-E2E-VISUAL): baselines
// are per platform and e2e runs only on release tags. The four cases assert layout invariants and
// keep a screenshot as a test artefact; docs images come from screenshots.spec.ts.
for (const [theme, vp] of [
  ["light", { width: 1280, height: 900 }],
  ["dark", { width: 1280, height: 900 }],
  ["light", { width: 390, height: 844 }],
  ["dark", { width: 390, height: 844 }],
] as const) {
  test(`layout invariants ${theme} ${vp.width}`, async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-10-10T07:00:00Z"));
    await page.emulateMedia({ colorScheme: theme });
    await page.setViewportSize(vp);
    await page.goto("/agent");
    const grid = page.getByTestId("dashboard-grid");
    await expect(grid).toBeVisible();
    const phone = vp.width < 520;
    await expect(grid).toHaveAttribute("data-breakpoint", phone ? "sm" : "lg");
    await expect(page.locator('[data-testid^="widget-"][data-type="core.counter"]')).toHaveCount(4);
    if (!phone) {
      await expect(page.getByTestId("widget-demo-w-open")).toHaveCSS("grid-column", "1 / span 3");
    }
    await expect(page.locator("[data-testid$='-handle']")).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath(`dashboard-view-${theme}-${vp.width}.png`) });

    await page.getByRole("button", { name: "Anpassen" }).click();
    const toolbar = page.getByRole("toolbar", { name: "Bearbeiten" });
    await expect(toolbar).toBeVisible();
    const bg = (el: Element) => getComputedStyle(el).backgroundColor;
    const toolbarBg = await toolbar.evaluate(bg);
    const pageBg = await page.locator("body").evaluate(bg);
    expect(toolbarBg).not.toBe(pageBg);
    await page.screenshot({ path: test.info().outputPath(`dashboard-edit-${theme}-${vp.width}.png`) });
  });
}
