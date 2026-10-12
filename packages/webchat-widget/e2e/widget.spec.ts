import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const TIQORA = "http://127.0.0.1:5190";
const HOST = "http://localhost:5191/host.html";
const DENIED_HOST = "http://localhost:5192/host.html";
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

/**
 * The launcher icon (path `index`: 0 chat bubble, 1 close cross) is really drawn: it has
 * geometry, wins against the host's `svg path` rules, and the launcher's centre pixel is the
 * white icon, not the button colour.
 */
async function expectIcon(page: Page, index: number) {
  const path = page.locator("button[aria-controls] svg path").nth(index);
  const icon = await path.evaluate((el) => {
    const p = el as SVGPathElement;
    const c = getComputedStyle(p);
    const b = p.getBBox();
    return { w: b.width, h: b.height, display: c.display, opacity: c.opacity, visibility: c.visibility, fill: c.fill, stroke: c.stroke };
  });
  expect(icon.w).toBeGreaterThan(10);
  expect(icon.h).toBeGreaterThan(10);
  expect(icon).toMatchObject({ display: "inline", opacity: "1", visibility: "visible" });
  expect(index === 0 ? icon.fill : icon.stroke).toBe("rgb(255, 255, 255)");
  const box = (await page.locator("button[aria-controls]").boundingBox())!;
  const shot = await page.screenshot({ clip: { x: box.x + box.width / 2 - 1, y: box.y + box.height / 2 - 1, width: 2, height: 2 } });
  const rgb = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = "data:image/png;base64," + b64;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(img, 0, 0);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2]];
  }, shot.toString("base64"));
  expect(Math.min(...rgb), `centre pixel ${rgb.join(",")} is not the white icon`).toBeGreaterThan(200);
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
  // the thumbnail is decorative (alt=""): the button's text already names the file
  const thumb = frame.locator(".tq-att", { hasText: "pixel.png" }).locator('img[alt=""]');
  await expect(thumb).toBeAttached();
  await expect.poll(() => thumb.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBe(1);
  await input.setInputFiles({
    name: "evil.svg",
    mimeType: "image/svg+xml",
    buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
  });
  await expect(frame.getByText("Diese Dateiart wird nicht angenommen.")).toBeVisible();
  await expect(frame.locator(".tq-att", { hasText: "evil.svg" })).toHaveCount(0);
});

test("launcher and panel survive hostile host CSS", async ({ page }) => {
  await page.goto(HOST);
  const launcher = page.getByRole("button", { name: LAUNCHER });
  await expect(launcher).toBeVisible();
  const view = page.viewportSize()!;
  const style = (sel: string) =>
    page.locator(sel).evaluate((el) => {
      const c = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {
        opacity: c.opacity,
        visibility: c.visibility,
        transform: c.transform,
        filter: c.filter,
        position: c.position,
        pointer: c.pointerEvents,
        box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      };
    });
  const l = await style("button[aria-controls]");
  expect(l).toMatchObject({ opacity: "1", visibility: "visible", transform: "none", filter: "none", position: "fixed", pointer: "auto" });
  expect(l.box).toEqual([view.width - 20 - 56, view.height - 20 - 56, 56, 56]);
  await expectIcon(page, 0); // the chat bubble is drawn, not an empty disc
  await launcher.click(); // really clickable
  const frame = frameOf(page);
  await expect(frame.getByText("Bevor es losgeht")).toBeVisible();
  await expectIcon(page, 1); // the close cross replaces it
  const p = await style("#tiqora-chat-panel");
  expect(p).toMatchObject({ opacity: "1", visibility: "visible", transform: "none", filter: "none", position: "fixed" });
  expect(p.box).toEqual([view.width - 20 - 380, view.height - 88 - 600, 380, 600]);
  const f = await style('iframe[title="Chat"]');
  expect(f).toMatchObject({ opacity: "1", visibility: "visible", transform: "none", position: "static" });
  expect(f.box).toEqual([p.box[0], p.box[1], 380, 600]);
  expect(await page.evaluate(() => document.body.scrollHeight <= innerHeight + 200)).toBe(true);
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

test("an iframe from a site that lies about its origin is blocked by frame-ancestors", async ({ page, request }) => {
  await page.goto(DENIED_HOST);
  await page.evaluate(() => {
    const f = document.createElement("iframe");
    f.referrerPolicy = "no-referrer"; // no Referer to give the lie away
    f.src = "http://127.0.0.1:5190/chat/v1/frame?k=wc_e2ekey&origin=" + encodeURIComponent("http://localhost:5191") + "&lang=de";
    document.body.appendChild(f);
  });
  await page.waitForTimeout(2500);
  const stats = await (await request.get(`${TIQORA}/__test/stats`)).json();
  // the server did answer (200 with frame-ancestors for 5191) but the browser refused to
  // render it on 5192: the frame never fetched a single asset
  expect(stats.frame200).toBeGreaterThan(0);
  expect(stats.assets).toBe(0);
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

// ---- chat assistant and website sign-in (Task 14) -------------------------------------------

const HANDOFF = "Mit einem Menschen sprechen";
const sessionState = async (request: APIRequestContext) =>
  (await (await request.get(`${TIQORA}/__test/session`)).json()) as {
    status: string;
    identity: { sub: string; name: string | null } | null;
    messages: { seq: number; sender: string; code: string | null; body: string | null }[];
  };

/** Waits for the launcher (= the frame said `ready`), then lets the customer site sign the visitor in or out. */
async function hostAction(page: Page, id: "identify" | "identify-other" | "identify-bad" | "logout") {
  await expect(page.getByRole("button", { name: LAUNCHER })).toBeVisible();
  // dispatched: the open chat panel may sit above the customer site's own controls
  await page.locator(`#${id}`).dispatchEvent("click");
}

/** Sends the first line from the pre-chat form of an anonymous visitor. */
async function startAnonymousChat(page: Page, text = "Wie setze ich mein Passwort zurück?") {
  await page.goto(HOST);
  await page.getByRole("button", { name: LAUNCHER }).click();
  const frame = frameOf(page);
  await frame.getByLabel("Name").fill("Erika Muster");
  await frame.getByLabel("Ihre Nachricht").fill(text);
  await frame.getByRole("button", { name: "Chat starten" }).click();
  return frame;
}

test("bot answers first and hands over on request", async ({ page, request }) => {
  await hook(request, "bot", { on: true });
  const frame = await startAnonymousChat(page);
  await expect(frame.getByText("Robin ist ein KI-Assistent und beantwortet Ihre Fragen.")).toBeVisible();
  const botLine = frame.locator(".tq-other", { hasText: "Das beantworte ich gern (1)." });
  await expect(botLine).toHaveCount(1);
  await expect(botLine.locator(".tq-name")).toContainText("Robin");
  await expect(botLine.locator(".tq-ai")).toHaveText("KI");
  await frame.getByRole("button", { name: HANDOFF }).click();
  await expect(frame.getByText("Wir verbinden Sie mit einem Mitarbeiter.")).toBeVisible();
  await expect(frame.getByRole("button", { name: HANDOFF })).toHaveCount(0);
  const s = await sessionState(request);
  expect(s.status).toBe("waiting");
  expect(s.messages.at(-1)).toMatchObject({ sender: "system", code: "handoff" });
  // the agent answers in the same chat afterwards
  await agentSays(request, "Hallo, hier ist ein Mensch");
  await expect(frame.getByText("Hallo, hier ist ein Mensch")).toBeVisible();
});

test("signed-in visitor skips name and e-mail", async ({ page, request }) => {
  await page.goto(HOST);
  await hostAction(page, "identify");
  await page.getByRole("button", { name: LAUNCHER }).click();
  const frame = frameOf(page);
  await expect(frame.getByText("Angemeldet als Erika Muster")).toBeVisible();
  await expect(frame.getByLabel("Name")).toHaveCount(0);
  await expect(frame.getByLabel("E-Mail")).toHaveCount(0);
  await frame.getByLabel("Ihre Nachricht").fill("Ich bin schon angemeldet");
  await frame.getByRole("button", { name: "Chat starten" }).click();
  await expect(frame.getByText("Ich bin schon angemeldet")).toBeVisible();
  await expect(frame.locator(".tq-signed")).toHaveText("Angemeldet als Erika Muster");
  expect((await sessionState(request)).identity).toEqual({ sub: "cust-1", name: "Erika Muster" });
});

test("a bad sign-in token is refused and the visitor is asked for the details", async ({ page, request }) => {
  await page.goto(HOST);
  await hostAction(page, "identify-bad");
  await page.getByRole("button", { name: LAUNCHER }).click();
  const frame = frameOf(page);
  await frame.getByLabel("Ihre Nachricht").fill("Hallo");
  await frame.getByRole("button", { name: "Chat starten" }).click();
  await expect(frame.getByText("Die Anmeldung konnte nicht bestätigt werden.")).toBeVisible();
  expect((await request.get(`${TIQORA}/__test/last-session`).then((r) => r.json())).session_id).toBeNull();
  // anonymous again: the name field is back and the chat starts
  await frame.getByLabel("Name").fill("Erika Muster");
  await frame.getByRole("button", { name: "Chat starten" }).click();
  await expect(frame.getByText("Hallo", { exact: true })).toBeVisible();
  expect((await sessionState(request)).identity).toBeNull();
});

test("a running chat that belongs to somebody else starts fresh after a sign-in", async ({ page, request }) => {
  const frame = await startChat(page);
  await hostAction(page, "identify"); // attaches the chat to cust-1
  await expect(frame.locator(".tq-signed")).toHaveText("Angemeldet als Erika Muster");
  expect((await sessionState(request)).identity?.sub).toBe("cust-1");
  await page.locator("#identify-other").dispatchEvent("click"); // another customer: identity_conflict
  await expect(frame.getByText("Bevor es losgeht")).toBeVisible();
  await expect(frame.getByText("Ich brauche Hilfe")).toHaveCount(0);
});

test("logout on a shared computer starts fresh", async ({ page }) => {
  await page.goto(HOST);
  await hostAction(page, "identify");
  await page.getByRole("button", { name: LAUNCHER }).click();
  const frame = frameOf(page);
  await frame.getByLabel("Ihre Nachricht").fill("Vertrauliche Frage");
  await frame.getByRole("button", { name: "Chat starten" }).click();
  await expect(frame.getByText("Vertrauliche Frage")).toBeVisible();
  await page.locator("#logout").dispatchEvent("click");
  // the transcript is gone at once, the pre-chat form asks for the details again
  await expect(frame.getByText("Vertrauliche Frage")).toHaveCount(0);
  await expect(frame.getByText("Bevor es losgeht")).toBeVisible();
  await expect(frame.getByLabel("Name")).toBeVisible();
  // a new page view of the next person on this computer does not bring the chat back
  await page.reload();
  await page.getByRole("button", { name: LAUNCHER }).click();
  await expect(frameOf(page).getByText("Bevor es losgeht")).toBeVisible();
  await expect(frameOf(page).getByText("Vertrauliche Frage")).toHaveCount(0);
});

test("a signed-in chat is not restored on a page view without a fresh sign-in", async ({ page }) => {
  await page.goto(HOST);
  await hostAction(page, "identify");
  await page.getByRole("button", { name: LAUNCHER }).click();
  const frame = frameOf(page);
  await frame.getByLabel("Ihre Nachricht").fill("Meine Rechnung");
  await frame.getByRole("button", { name: "Chat starten" }).click();
  await expect(frame.getByText("Meine Rechnung")).toBeVisible();
  await expect(frame.locator(".tq-signed")).toBeVisible(); // the server confirmed who the chat belongs to
  await page.reload(); // the host does not sign in again
  await page.getByRole("button", { name: LAUNCHER }).click();
  await expect(frameOf(page).getByText("Bevor es losgeht")).toBeVisible();
  await expect(frameOf(page).getByText("Meine Rechnung")).toHaveCount(0);
});

test("reconnect during a bot turn keeps one answer", async ({ page, request }) => {
  await hook(request, "bot", { on: true, hold: true });
  const frame = await startAnonymousChat(page);
  await expect(frame.getByText("Robin schreibt")).toBeVisible(); // the bot is typing, its line is parked
  const dropped = await (await hook(request, "drop-streams")).json();
  expect(dropped.dropped).toBeGreaterThan(0);
  const released = await (await hook(request, "bot-release")).json(); // the line lands while the stream is down
  expect(released.released).toBe(1);
  await expect(frame.getByText("Das beantworte ich gern (1).")).toHaveCount(1);
  await page.waitForTimeout(2500); // a late replay would double it
  await expect(frame.getByText("Das beantworte ich gern (1).")).toHaveCount(1);
  expect((await sessionState(request)).messages.filter((m) => m.sender === "bot")).toHaveLength(1);
});

test("the handover button is reachable with the keyboard alone", async ({ page, request }) => {
  await hook(request, "bot", { on: true });
  const frame = await startAnonymousChat(page);
  await expect(frame.getByText("Das beantworte ich gern (1).")).toBeVisible();
  const button = frame.getByRole("button", { name: HANDOFF });
  await expect(button).toHaveAccessibleName(HANDOFF);
  await frame.locator(".tq-input").focus();
  let reached = false;
  for (let i = 0; i < 6 && !reached; i++) {
    await page.keyboard.press("Tab");
    reached = await button.evaluate((el) => el === el.ownerDocument.activeElement);
  }
  expect(reached, "Tab never reached the handover button").toBe(true);
  await page.keyboard.press("Enter");
  await expect(frame.getByText("Wir verbinden Sie mit einem Mitarbeiter.")).toBeVisible();
  expect((await sessionState(request)).status).toBe("waiting");
});

test("a portal customer is recognised without the host", async ({ page, request }) => {
  await hook(request, "portal", { token: "e30.eyJuYW1lIjoiRXJpa2EgTXVzdGVyIiwic3ViIjoiY3VzdC0xIn0.mocksig", name: "Erika Muster" });
  await page.goto(HOST);
  await page.getByRole("button", { name: LAUNCHER }).click();
  const frame = frameOf(page);
  await expect(frame.getByText("Angemeldet als Erika Muster")).toBeVisible();
  await expect(frame.getByLabel("Name")).toHaveCount(0);
});
