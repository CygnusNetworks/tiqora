import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const TIQORA = "http://127.0.0.1:5190";
const HOST = "http://127.0.0.1:5191/host.html";
const DENIED_HOST = "http://127.0.0.1:5192/host.html";
const LAUNCHER = "Fragen? Schreiben Sie uns";
// 1x1 transparent PNG
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

const hook = (request: APIRequestContext, name: string, data: object = {}) =>
  request.post(`${TIQORA}/__test/${name}`, { data });
const frameOf = (page: Page) => page.frameLocator('iframe[title="Chat"]');

async function agentSays(request: APIRequestContext, body: string) {
  expect((await hook(request, "agent-message", { body })).ok()).toBe(true);
}

/** Opens the launcher and starts a chat through the pre-chat form. */
async function startChat(page: Page) {
  await page.goto(HOST);
  await page.getByRole("button", { name: LAUNCHER }).click();
  const frame = frameOf(page);
  await expect(frame.getByText("Bevor es losgeht")).toBeVisible();
  await frame.getByLabel("Name").fill("Erika Muster");
  await frame.getByLabel("Ihre Nachricht").fill("Ich brauche Hilfe");
  await frame.getByRole("button", { name: "Chat starten" }).click();
  await expect(frame.getByText("Ich brauche Hilfe")).toBeVisible();
  return frame;
}

test.beforeEach(async ({ request }) => {
  await hook(request, "reset");
});

test("opens, pre-chat, chats with an agent", async ({ page, request }) => {
  const frame = await startChat(page);
  await agentSays(request, "Hallo, wie kann ich helfen?");
  await expect(frame.getByText("Hallo, wie kann ich helfen?")).toBeVisible();
});

test("reconnects after a dropped stream without losing or doubling messages", async ({ page, request }) => {
  const frame = await startChat(page);
  await agentSays(request, "Erste");
  await expect(frame.getByText("Erste", { exact: true })).toBeVisible();
  const dropped = await (await hook(request, "drop-streams")).json();
  expect(dropped.dropped).toBeGreaterThan(0);
  await agentSays(request, "Zweite"); // while the visitor is disconnected
  await expect(frame.getByText("Zweite", { exact: true })).toHaveCount(1);
  await page.waitForTimeout(2500); // a late replay would double it
  await expect(frame.getByText("Zweite", { exact: true })).toHaveCount(1);
  await expect(frame.getByText("Erste", { exact: true })).toHaveCount(1);
});

test("offline form when nobody is online", async ({ page, request }) => {
  await hook(request, "offline", { on: true });
  await page.goto(HOST);
  await page.getByRole("button", { name: LAUNCHER }).click();
  const frame = frameOf(page);
  await expect(frame.getByText("Gerade ist niemand im Chat")).toBeVisible();
  await frame.getByLabel("Name").fill("Erika Muster");
  await frame.getByLabel("E-Mail").fill("erika@example.org");
  await frame.getByLabel("Ihre Nachricht").fill("Bitte um Rückruf");
  await frame.getByRole("button", { name: "Nachricht senden" }).click();
  await expect(frame.getByText("Danke!")).toBeVisible();
});

test("uploads a PNG and refuses an SVG", async ({ page }) => {
  const frame = await startChat(page);
  const input = frame.locator('input[type="file"]');
  await input.setInputFiles({ name: "pixel.png", mimeType: "image/png", buffer: PNG });
  const thumb = frame.locator('img[alt="pixel.png"]');
  await expect(thumb).toBeAttached();
  await expect.poll(() => thumb.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBe(1);
  await input.setInputFiles({
    name: "evil.svg",
    mimeType: "image/svg+xml",
    buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
  });
  await expect(frame.getByText("Diese Dateiart wird nicht angenommen.")).toBeVisible();
  await expect(frame.locator('img[alt="evil.svg"]')).toHaveCount(0);
});

test("RTL for Arabic pages", async ({ page }) => {
  await page.goto(`${HOST}?lang=ar`);
  await expect(page.locator("button[aria-controls]")).toBeVisible();
  await expect(frameOf(page).locator("html")).toHaveAttribute("dir", "rtl");
});

test("is not shown on a site that is not allowed", async ({ page }) => {
  await page.goto(DENIED_HOST);
  // the loader embeds the frame after the idle delay; the server refuses it, so no ready
  await expect(page.locator('iframe[title="Chat"]')).toBeAttached();
  await page.waitForTimeout(3000);
  await expect(page.locator("button[aria-controls]")).toHaveCount(0);
  await expect(page.locator("#tiqora-chat-panel")).toBeHidden();
});

test("a second loader embed does nothing", async ({ page }) => {
  await page.goto(HOST);
  await expect(page.locator("button[aria-controls]")).toBeVisible();
  await page.evaluate(() => {
    const s = document.createElement("script");
    s.src = "http://127.0.0.1:5190/chat/v1/loader.js";
    s.dataset.tiqoraChat = "wc_e2ekey";
    document.body.appendChild(s);
  });
  await page.waitForTimeout(2000);
  await expect(page.locator('iframe[title="Chat"]')).toHaveCount(1);
  await expect(page.locator("button[aria-controls]")).toHaveCount(1);
});

test("host API opens and reports messages without content", async ({ page, request }) => {
  type W = {
    Tiqora: { chat: { open(): void; close(): void; on(e: string, cb: (d: unknown) => void): () => void } };
    __got?: unknown;
    __events: string[];
  };
  await page.goto(HOST);
  await expect(page.locator("button[aria-controls]")).toBeVisible();
  await page.evaluate(() => {
    const w = window as unknown as W;
    w.__events = [];
    w.Tiqora.chat.on("message", (d) => (w.__got = d));
    w.Tiqora.chat.on("open", () => w.__events.push("open"));
    w.Tiqora.chat.on("close", () => w.__events.push("close"));
    w.Tiqora.chat.open();
  });
  const frame = frameOf(page);
  await expect(frame.getByText("Bevor es losgeht")).toBeVisible();
  await frame.getByLabel("Name").fill("Erika Muster");
  await frame.getByLabel("Ihre Nachricht").fill("Hallo");
  await frame.getByRole("button", { name: "Chat starten" }).click();
  await expect(frame.getByText("Hallo", { exact: true })).toBeVisible();
  await agentSays(request, "Geheime Antwort");
  await expect.poll(() => page.evaluate(() => (window as unknown as W).__got)).toEqual({ sender: "agent" });
  // closing from the frame (Escape) reaches the host API and the launcher
  await frame.locator(".tq-input").press("Escape");
  await expect.poll(() => page.evaluate(() => (window as unknown as W).__events)).toEqual(["open", "close"]);
  await expect(page.locator("button[aria-controls]")).toHaveAttribute("aria-expanded", "false");
  // nothing the page can reach carries the session token
  const leaked = await page.evaluate(() =>
    JSON.stringify([Object.keys((window as unknown as W).Tiqora.chat), document.cookie, JSON.stringify(localStorage)]),
  );
  expect(leaked).not.toMatch(/token|\.[A-Za-z0-9_-]{30,}/);
});
