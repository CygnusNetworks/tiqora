import { describe, expect, it, vi } from "vitest";
import { act } from "preact/test-utils";
import { renderPreact } from "../test/render";
import { App, type ChatApi, type FrameParams } from "./App";
import { ChatApiError } from "./api";
import type { FrameBridge } from "./bridge";
import { loadMessages } from "./i18n";
import type { HostToFrame } from "../shared/protocol";
import type { SharedStreamOptions } from "./shared-stream";
import type { SseEvent } from "./sse";
import { loadSession, saveSession, type Store } from "./storage";
import { byLabel, byText, cfg, click, flush, input, keydown, msg } from "./testkit";

const params: FrameParams = { k: "wc_key", origin: "https://host.example", lang: "de", preview: false };

function memStore(): Store {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v), remove: (k) => void m.delete(k) };
}

function fakeApi(over: Record<string, unknown> = {}) {
  const api = {
    streamUrl: (id: string) => `/s/${id}/stream`,
    config: vi.fn(async () => cfg()),
    createSession: vi.fn(async (_b: Record<string, unknown>) => ({
      session_id: "sid1",
      token: "tok1",
      status: "waiting",
      message: msg({ seq: 1, sender: "visitor", body: "Hilfe", client_id: "c0" }),
    })),
    session: vi.fn(async () => ({ agent_name: null, can_send: true, last_seq: 5, needs_contact: false, session_id: "sid1", status: "active", topic_label: null })),
    messages: vi.fn(async () => []),
    send: vi.fn(async (_i: string, _t: string, body: string, clientId: string) => msg({ seq: 9, sender: "visitor", body, client_id: clientId })),
    typing: vi.fn(async () => undefined),
    upload: vi.fn(async () => msg({ seq: 10 })),
    download: vi.fn(async () => new Blob(["x"])),
    contact: vi.fn(async () => undefined),
    end: vi.fn(async () => undefined),
    offline: vi.fn(async (_b: Record<string, unknown>) => undefined),
    ...over,
  };
  return api;
}

function fakeStream() {
  const made: Array<{ o: SharedStreamOptions; start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }> = [];
  const create = (o: SharedStreamOptions) => {
    const s = { o, start: vi.fn(), stop: vi.fn() };
    made.push(s);
    return s;
  };
  const emit = (event: string, data: unknown, id: string | null = null) =>
    act(() => {
      made[made.length - 1].o.onEvent({ id, event, data: JSON.stringify(data) } satisfies SseEvent);
    });
  return { made, create, emit };
}

async function mount(opts: { api?: ReturnType<typeof fakeApi>; store?: Store; p?: Partial<FrameParams>; bridge?: FrameBridge } = {}) {
  const api = opts.api ?? fakeApi();
  const store = opts.store ?? memStore();
  const post = vi.fn();
  const host: { send?: (m: HostToFrame) => void } = {};
  const bridge: FrameBridge = opts.bridge ?? { post, dispose: () => undefined };
  const stream = fakeStream();
  const t = await loadMessages("de");
  const { container } = renderPreact(
    <App params={{ ...params, ...opts.p }} api={api as unknown as ChatApi} t={t} store={store} bridge={bridge} createStream={stream.create} />,
  );
  await flush();
  return { container, api, store, post, stream, host };
}

describe("App", () => {
  it("pre-chat → session → agent reply → end", async () => {
    const { container, api, post, stream, store } = await mount();
    input(byLabel(container, "Name"), "Erika");
    input(byLabel(container, "E-Mail"), "e@example.org");
    input(byLabel(container, "Ihre Nachricht"), "Hilfe");
    click(byText(container, "button", "Chat starten"));
    await flush();

    const body = api.createSession.mock.calls[0][0] as { prechat: unknown; message: string; visitor_id: string; client_id: string };
    expect(body.prechat).toEqual({ name: "Erika", email: "e@example.org" });
    expect(body.message).toBe("Hilfe");
    expect(body.visitor_id).toHaveLength(22);
    expect(typeof body.client_id).toBe("string");
    expect(container.querySelector(".tq-status")?.textContent).toBe("Einen Moment – gleich ist jemand für Sie da.");
    expect(loadSession("wc_key", store)).toEqual({ sessionId: "sid1", token: "tok1", lastSeq: 1, identified: false });
    expect(stream.made[0].start).toHaveBeenCalled();
    expect(stream.made[0].o.token).toBe("tok1");

    stream.emit("session", { status: "active", agent_name: "Max" });
    stream.emit("message", msg({ seq: 2, sender: "agent", body: "Hallo!", agent_name: "Max" }), "2");
    expect(container.textContent).toContain("Max ist im Chat");
    expect(container.querySelector(".tq-other .tq-bubble")?.textContent).toBe("Hallo!");
    expect(post).toHaveBeenCalledWith({ p: "tiqora-chat/1", type: "message", sender: "agent" });
    expect(JSON.stringify(post.mock.calls)).not.toContain("Hallo!");

    // a bot (AI) message is shown like an agent message, and the host is told "bot" (no text)
    expect(post.mock.calls.filter((c) => c[0].type === "message")).toHaveLength(1);
    stream.emit("message", msg({ seq: 3, sender: "bot", body: "Ich bin die KI", agent_name: null }), "3");
    expect(container.textContent).toContain("Ich bin die KI");
    expect(post.mock.calls.filter((c) => c[0].type === "message")).toHaveLength(2);
    expect(post.mock.calls.filter((c) => c[0].type === "message").map((c) => c[0].sender)).toEqual(["agent", "bot"]);

    click(byText(container, "button", "Chat beenden"));
    await flush();
    expect(api.end).toHaveBeenCalledWith("sid1", "tok1");
    stream.emit("end", { reason: "something_new" });
    expect(byText(container, "button", "Neuen Chat starten")).toBeTruthy();
    expect(container.querySelector(".tq-bubble")).toBeTruthy(); // transcript stays

    click(byText(container, "button", "Neuen Chat starten"));
    expect(loadSession("wc_key", store)).toBeNull();
    expect(byLabel(container, "Name")).toBeTruthy();
  });

  it("restores a stored session, fetches the transcript and resumes the stream at lastSeq", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 5 }, store);
    const api = fakeApi({ messages: vi.fn(async () => [msg({ seq: 1 }), msg({ seq: 2, sender: "visitor" })]) });
    const { container, stream } = await mount({ api, store });
    expect(api.session).toHaveBeenCalledWith("sid1", "tok1");
    expect(api.messages).toHaveBeenCalledWith("sid1", "tok1", 0);
    expect(container.querySelectorAll(".tq-msg")).toHaveLength(2);
    expect(stream.made[0].o.lastSeq()).toBe(2); // resumes from what was loaded, not the stored cursor
    expect(loadSession("wc_key", store)?.lastSeq).toBe(5); // the persisted cursor never goes down
  });

  it("a stream event racing the restore is deduped and lastSeq never goes down (M12)", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 7 }, store);
    const api = fakeApi({ messages: vi.fn(async () => [msg({ seq: 1 }), msg({ seq: 2 }), msg({ seq: 3 })]) });
    const { container, stream } = await mount({ api, store });
    stream.emit("message", msg({ seq: 3 }), "3");
    stream.emit("message", msg({ seq: 3 }), "3");
    expect(container.querySelectorAll(".tq-msg")).toHaveLength(3);
    expect(loadSession("wc_key", store)?.lastSeq).toBe(7); // another tab was ahead: kept
    stream.emit("message", msg({ seq: 8 }), "8");
    expect(loadSession("wc_key", store)?.lastSeq).toBe(8);
  });

  it("an own send during a stream outage does not skip the agent reply sent meanwhile (F-I2)", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 4 }, store);
    const server = [
      msg({ seq: 1, sender: "visitor", body: "Hilfe" }),
      msg({ seq: 2, sender: "agent", body: "Hallo" }),
      msg({ seq: 3, sender: "visitor", body: "Router blinkt" }),
      msg({ seq: 4, sender: "agent", body: "Moment" }),
    ];
    const send = vi.fn(async (_i: string, _t: string, body: string, clientId: string) => {
      const m = msg({ seq: server.length + 1, sender: "visitor", body, client_id: clientId });
      server.push(m);
      return m;
    });
    const api = fakeApi({ messages: vi.fn(async () => server.slice(0, 4)), send });
    const { container, stream } = await mount({ api, store });
    expect(stream.made[0].o.lastSeq()).toBe(4);
    // the stream is down; the agent answers (seq 5), then the visitor sends (POST → seq 6)
    server.push(msg({ seq: 5, sender: "agent", body: "Bitte neu starten" }));
    const ta = container.querySelector("textarea") as HTMLTextAreaElement;
    input(ta, "Noch da?");
    keydown(ta, "Enter");
    await flush();
    expect(container.textContent).toContain("Noch da?");
    // reconnect: the server replays everything after Last-Event-ID
    const resume = stream.made[0].o.lastSeq();
    expect(resume).toBe(4);
    for (const m of server.filter((x) => x.seq > resume)) stream.emit("message", m, String(m.seq));
    expect(byText(container, ".tq-bubble", "Bitte neu starten")).toBeTruthy();
    expect(container.querySelectorAll(".tq-msg")).toHaveLength(6); // nothing doubled
    expect(stream.made[0].o.lastSeq()).toBe(6);
    expect(loadSession("wc_key", store)?.lastSeq).toBe(6);
  });

  it("a stale 404 session is dropped and the pre-chat form shows", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "gone", token: "t", lastSeq: 1 }, store);
    const api = fakeApi({ session: vi.fn(async () => Promise.reject(new ChatApiError(404, "not_found"))) });
    const { container } = await mount({ api, store });
    expect(loadSession("wc_key", store)).toBeNull();
    expect(byLabel(container, "Name")).toBeTruthy();
  });

  it("reports ready only after a successful config, nothing on 404 or 403", async () => {
    const ok = await mount();
    expect(ok.post).toHaveBeenCalledTimes(1);
    expect(ok.post.mock.calls[0][0]).toMatchObject({
      type: "ready", online: true, hidden: false, launcherLabel: "Fragen? Schreiben Sie uns", dir: "ltr",
      theme: { primaryColor: "#2563eb", position: "right", mode: "auto" },
    });

    for (const status of [404, 403, 0]) {
      const api = fakeApi({ config: vi.fn(async () => Promise.reject(new ChatApiError(status, "x"))) });
      const r = await mount({ api });
      expect(r.post).not.toHaveBeenCalled();
    }
    const hidden = await mount({ api: fakeApi({ config: vi.fn(async () => cfg({ online: false, offline_mode: "hide" })) }) });
    expect(hidden.post.mock.calls[0][0]).toMatchObject({ type: "ready", online: false, hidden: true });
  });

  it("blocked shows the not-allowed text", async () => {
    const api = fakeApi({ config: vi.fn(async () => Promise.reject(new ChatApiError(403, "forbidden"))) });
    const { container } = await mount({ api });
    expect(container.textContent).toContain("Dieser Chat ist für diese Website nicht freigegeben.");
  });

  it("offline form sends the message with email and shows thanks", async () => {
    const api = fakeApi({ config: vi.fn(async () => cfg({ online: false })) });
    const { container } = await mount({ api });
    expect(container.textContent).toContain("Gerade ist niemand im Chat");
    input(byLabel(container, "Name"), "Erika");
    input(byLabel(container, "E-Mail"), "e@example.org");
    input(byLabel(container, "Ihre Nachricht"), "Bitte melden");
    click(byText(container, "button", "Nachricht senden"));
    await flush();
    expect(api.offline.mock.calls[0][0]).toMatchObject({ email: "e@example.org", name: "Erika", message: "Bitte melden" });
    expect(container.textContent).toContain("Danke! Wir melden uns per E-Mail.");
  });

  it("shows friendly messages for send errors and treats disabled as ended", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const send = vi.fn()
      .mockRejectedValueOnce(new ChatApiError(429, "rate_limited", {}, 30))
      .mockRejectedValueOnce(new ChatApiError(413, "body_too_large"))
      .mockRejectedValueOnce(new ChatApiError(404, "disabled"));
    const { container } = await mount({ api: fakeApi({ send }), store });
    const ta = () => container.querySelector("textarea") as HTMLTextAreaElement;
    input(ta(), "eins");
    keydown(ta(), "Enter");
    await flush();
    expect(container.textContent).toContain("Zu viele Nachrichten – bitte kurz warten.");
    expect(container.textContent).toContain("Nicht gesendet – try again".replace("try again", "erneut versuchen"));
    click(byText(container, "button", "Erneut senden"));
    await flush();
    expect(container.textContent).toContain("Die Nachricht ist zu groß.");
    click(byText(container, "button", "Erneut senden"));
    await flush();
    expect(container.textContent).toContain("Der Chat wurde beendet, Sie können nichts mehr senden.");
    expect(byText(container, "button", "Neuen Chat starten")).toBeTruthy();
  });

  it("maps upload_rejected codes", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const upload = vi.fn(async () => Promise.reject(new ChatApiError(422, "upload_rejected", { file: "size" })));
    const { container } = await mount({ api: fakeApi({ upload }), store });
    const fileInput = container.querySelector("input[type=file]") as HTMLInputElement;
    act(() => {
      Object.defineProperty(fileInput, "files", { value: [new File([new Uint8Array(10)], "a.png", { type: "image/png" })], configurable: true });
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    expect(upload).toHaveBeenCalled();
    expect(container.textContent).toContain("Die Datei ist zu groß (höchstens 1 KB).");
  });

  it("an ended no_agent chat without email asks for the address", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 3 }, store);
    const api = fakeApi({
      session: vi.fn(async () => ({ agent_name: null, can_send: false, last_seq: 3, needs_contact: true, session_id: "sid1", status: "ended", topic_label: null })),
      messages: vi.fn(async () => [msg({ seq: 3, sender: "system", code: "no_agent", body: "no_agent", params: { has_email: false } })]),
    });
    const { container } = await mount({ api, store });
    expect(container.textContent).toContain("Wohin dürfen wir Ihnen antworten?");
    input(container.querySelector("#tq-contact-email") as Element, "e@example.org");
    click(byText(container, "button", "Speichern"));
    await flush();
    expect(api.contact).toHaveBeenCalledWith("sid1", "tok1", "e@example.org", null);
    expect(container.textContent).toContain("Danke, wir antworten per E-Mail.");
  });

  it("an ended chat under offline_mode hide offers no restart into an empty panel (T16 M11)", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const api = fakeApi({
      config: vi.fn(async () => cfg({ online: false, offline_mode: "hide" })),
      session: vi.fn(async () => ({ agent_name: null, can_send: false, last_seq: 1, needs_contact: false, session_id: "sid1", status: "ended", topic_label: null })),
      messages: vi.fn(async () => [msg({ seq: 1, sender: "visitor", body: "Hallo" })]),
    });
    const { container } = await mount({ api, store });
    expect(container.textContent).toContain("Hallo");
    expect(container.textContent).not.toContain("Neuen Chat starten");
    expect(container.querySelector(".tq-foot .tq-notice")?.textContent).toBe("Gerade ist niemand im Chat");
    expect(loadSession("wc_key", store)?.sessionId).toBe("sid1");
  });

  it("Escape asks the host to close; unread is reported while closed", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const { stream, post } = await mount({ store });
    keydown(document.body, "Escape");
    expect(post).toHaveBeenCalledWith({ p: "tiqora-chat/1", type: "close-request" });
    stream.emit("message", msg({ seq: 2 }), "2");
    expect(post).toHaveBeenCalledWith({ p: "tiqora-chat/1", type: "unread", count: 1 });
  });

  it("focus follows the open panel and the view, never while closed", async () => {
    const t = await loadMessages("de");
    const hostMsg = (type: string) =>
      act(() => {
        window.dispatchEvent(new MessageEvent("message", { origin: "https://host.example", source: window, data: { p: "tiqora-chat/1", type } }));
      });
    (document.activeElement as HTMLElement | null)?.blur();
    const { container } = renderPreact(<App params={params} api={fakeApi() as unknown as ChatApi} t={t} store={memStore()} createStream={fakeStream().create} />);
    await flush();
    expect(document.activeElement).toBe(document.body); // closed: no focus grab
    hostMsg("open");
    expect(document.activeElement).toBe(byLabel(container, "Name"));
    input(byLabel(container, "Name"), "Erika");
    input(byLabel(container, "E-Mail"), "e@example.org");
    input(byLabel(container, "Ihre Nachricht"), "Hilfe");
    click(byText(container, "button", "Chat starten"));
    await flush();
    expect(document.activeElement).toBe(container.querySelector(".tq-input"));
    click(byText(container, "button", "Chat beenden"));
    await flush();
    expect(document.activeElement).toBe(byText(container, "button", "Neuen Chat starten"));
  });

  it("restoring a chat while closed does not take focus; opening focuses the composer", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    (document.activeElement as HTMLElement | null)?.blur();
    const t = await loadMessages("de");
    const { container } = renderPreact(<App params={params} api={fakeApi() as unknown as ChatApi} t={t} store={store} createStream={fakeStream().create} />);
    await flush();
    expect(document.activeElement).toBe(document.body);
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { origin: "https://host.example", source: window, data: { p: "tiqora-chat/1", type: "open" } }));
    });
    expect(document.activeElement).toBe(container.querySelector(".tq-input"));
  });

  it("renders RTL", async () => {
    const { container } = await mount({ p: { lang: "ar" } });
    expect(container.querySelector(".tq-root")?.getAttribute("dir")).toBe("rtl");
    expect(document.documentElement.getAttribute("dir")).toBe("rtl");
  });

  it("preview mode calls only config and applies preview-config", async () => {
    const api = fakeApi();
    const t = await loadMessages("de");
    const { container } = renderPreact(
      <App params={{ ...params, preview: true }} api={api as unknown as ChatApi} t={t} store={memStore()}
        bridge={{ post: vi.fn(), dispose: () => undefined }} createStream={fakeStream().create} />,
    );
    await flush();
    input(byLabel(container, "Name"), "Erika");
    input(byLabel(container, "E-Mail"), "e@example.org");
    input(byLabel(container, "Ihre Nachricht"), "Vorschau");
    click(byText(container, "button", "Chat starten"));
    await flush();
    expect(container.textContent).toContain("Hallo! Wie kann ich Ihnen helfen?"); // webchatWidget.preview.reply
    input(container.querySelector("textarea") as Element, "noch eine");
    keydown(container.querySelector("textarea") as Element, "Enter");
    await flush();
    expect(api.config).toHaveBeenCalledTimes(1);
    for (const name of ["createSession", "session", "messages", "send", "typing", "upload", "end", "offline", "contact"] as const) {
      expect(api[name]).not.toHaveBeenCalled();
    }
    expect(container.textContent).toContain("noch eine");
  });

  it("applies preview-config from the host window only in preview mode and only from its origin", async () => {
    const pc = {
      theme: { primary_color: "#112233", position: "left", mode: "dark", launcher_label: null },
      greeting: "Servus",
      prechat_fields: [{ key: "company", kind: "text", label: "Firma", required: false, options: [] }],
      topics: [],
    };
    const fromHost = (origin: string) =>
      act(() => {
        window.dispatchEvent(new MessageEvent("message", { origin, source: window, data: { p: "tiqora-chat/1", type: "preview-config", config: pc } }));
      });
    const t = await loadMessages("de");
    const { container } = renderPreact(<App params={{ ...params, preview: true }} api={fakeApi() as unknown as ChatApi} t={t} store={memStore()} />);
    await flush();
    fromHost("https://evil.example");
    expect(container.textContent).not.toContain("Servus");
    fromHost("https://host.example");
    expect(container.textContent).toContain("Servus");
    expect(byLabel(container, "Firma")).toBeTruthy();
    expect(document.documentElement.style.getPropertyValue("--tq-primary")).toBe("#112233");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    const live = renderPreact(<App params={params} api={fakeApi() as unknown as ChatApi} t={t} store={memStore()} />);
    await flush();
    fromHost("https://host.example");
    expect(live.container.textContent).not.toContain("Firma");
  });

  // ---- chat assistant and recognised visitors (C9) -------------------------------------

  const TOKEN = "aaa.bbb.ccc";
  const session = (over: Record<string, unknown> = {}) => ({
    agent_name: null, bot_name: null, can_handoff: false, can_send: true, identified: false, identified_as: null,
    last_seq: 1, needs_contact: false, session_id: "sid1", status: "active", topic_label: null, ...over,
  });
  const botCreate = () =>
    vi.fn(async (_b: Record<string, unknown>) => ({
      session_id: "sid1", token: "tok1", status: "bot",
      message: msg({ seq: 1, sender: "visitor", body: "Hilfe", client_id: "c0" }),
    }));
  const toHost = (data: unknown, origin = "https://host.example") =>
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { origin, source: window, data }));
    });
  /** Mounts with the real bridge (so the host can speak) and records what the frame posts. */
  async function mountHost(opts: { api?: ReturnType<typeof fakeApi>; store?: Store; p?: Partial<FrameParams> } = {}) {
    const api = opts.api ?? fakeApi();
    const store = opts.store ?? memStore();
    const stream = fakeStream();
    const posted: Array<Record<string, unknown>> = [];
    const spy = vi.spyOn(window, "postMessage").mockImplementation(((m: Record<string, unknown>) => void posted.push(m)) as never);
    const t = await loadMessages("de");
    const { container } = renderPreact(
      <App params={{ ...params, ...opts.p }} api={api as unknown as ChatApi} t={t} store={store} createStream={stream.create} />,
    );
    await flush();
    return { container, api, store, stream, posted, spy };
  }
  const fillAndStart = async (container: HTMLElement, name = true) => {
    if (name) {
      input(byLabel(container, "Name"), "Erika");
      input(byLabel(container, "E-Mail"), "e@example.org");
    }
    input(byLabel(container, "Ihre Nachricht"), "Hilfe");
    click(byText(container, "button", "Chat starten"));
    await flush();
  };

  it("a bot chat: header, named bot lines with a KI badge, intro from params, typing, handover button", async () => {
    const api = fakeApi({ createSession: botCreate() });
    const { container, post, stream } = await mount({ api });
    await fillAndStart(container);
    expect(container.querySelector(".tq-status")?.textContent).toBe("KI-Assistent antwortet");
    stream.emit("session", { status: "bot", agent_name: null, bot_name: "Robo" });
    expect(container.querySelector(".tq-status")?.textContent).toBe("Robo antwortet");
    stream.emit("message", msg({ seq: 2, sender: "system", body: "bot_intro", code: "bot_intro", params: { name: "Robo", notice: "Antworten können Fehler enthalten." } }), "2");
    expect(container.textContent).toContain("Robo ist ein KI-Assistent");
    expect(container.textContent).toContain("Antworten können Fehler enthalten.");
    stream.emit("typing", { who: "bot" });
    expect(container.querySelector(".tq-typing")?.textContent).toBe("Robo schreibt …");
    stream.emit("message", msg({ seq: 3, sender: "bot", body: "Starten Sie neu." }), "3");
    expect(container.querySelector(".tq-typing")).toBeNull();
    expect(container.querySelector(".tq-name")?.textContent).toBe("Robo KI");
    expect(container.querySelector(".tq-ai")?.textContent).toBe("KI");
    expect(post).toHaveBeenCalledWith({ p: "tiqora-chat/1", type: "message", sender: "bot" });
    stream.emit("typing", { who: "agent" });
    expect(container.querySelector(".tq-typing")?.textContent).toBe("schreibt …");
    expect(container.querySelector(".tq-composer, .tq-input")).toBeTruthy(); // the visitor can type to the bot
  });

  it("the handover button posts handoff once, stays disabled while running, and goes with the waiting event", async () => {
    let release: () => void = () => undefined;
    const handoff = vi.fn(() => new Promise<void>((r) => (release = r)));
    const api = fakeApi({ createSession: botCreate(), handoff });
    const { container, stream } = await mount({ api });
    expect(container.querySelector(".tq-handoff")).toBeNull();
    await fillAndStart(container);
    const btn = byText(container, "button", "Mit einem Menschen sprechen") as HTMLButtonElement;
    click(btn);
    await flush();
    click(container.querySelector(".tq-handoff") as Element);
    expect(handoff).toHaveBeenCalledTimes(1);
    expect(handoff).toHaveBeenCalledWith("sid1", "tok1");
    expect((container.querySelector(".tq-handoff") as HTMLButtonElement).disabled).toBe(true);
    release();
    await flush();
    expect(container.querySelector(".tq-handoff")).not.toBeNull(); // only the session event removes it
    stream.emit("session", { status: "waiting", agent_name: null });
    expect(container.querySelector(".tq-handoff")).toBeNull();
    expect(container.querySelector(".tq-status")?.textContent).toBe("Einen Moment – gleich ist jemand für Sie da.");
    stream.emit("message", msg({ seq: 2, sender: "system", body: "handoff", code: "handoff", params: { reason: "visitor" } }), "2");
    expect(container.textContent).toContain("Wir verbinden Sie mit einem Mitarbeiter.");
  });

  it("a refused handover (an agent was faster) refreshes the status instead of failing", async () => {
    const api = fakeApi({
      createSession: botCreate(),
      handoff: vi.fn(async () => {
        throw new ChatApiError(409, "not_bot");
      }),
      session: vi.fn(async () => session({ status: "active", agent_name: "Max", last_seq: 2 })),
    });
    const { container } = await mount({ api });
    await fillAndStart(container);
    click(byText(container, "button", "Mit einem Menschen sprechen"));
    await flush();
    expect(container.querySelector(".tq-handoff")).toBeNull();
    expect(container.querySelector(".tq-banner")).toBeNull();
  });

  it("identify before the start hides name/e-mail, sends the token and shows who is signed in", async () => {
    const api = fakeApi({
      createSession: botCreate(),
      session: vi.fn(async () => session({ status: "bot", can_handoff: true, identified_as: "Erika Muster" })),
    });
    const { container, store, posted } = await mountHost({ api });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    toHost({ p: "tiqora-chat/1", type: "open" });
    expect(() => byLabel(container, "Name")).toThrow();
    expect(() => byLabel(container, "E-Mail")).toThrow();
    await fillAndStart(container, false);
    const body = api.createSession.mock.calls[0][0] as { identity?: string; prechat: unknown };
    expect(body.identity).toBe(TOKEN);
    expect(body.prechat).toEqual({});
    expect(container.textContent).toContain("Angemeldet als Erika Muster");
    expect(posted).toContainEqual({ p: "tiqora-chat/1", type: "identity", verified: true });
    expect(loadSession("wc_key", store)?.identified).toBe(true);
    expect(store.get("tiqora-chat:session:wc_key")).not.toContain(TOKEN); // the token is never stored
  });

  it("a refused sign-in on create shows the full form again with the message", async () => {
    const api = fakeApi({
      createSession: vi.fn(async () => {
        throw new ChatApiError(422, "invalid_identity");
      }),
    });
    const { container, posted } = await mountHost({ api });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    await fillAndStart(container, false);
    expect(container.querySelector("[role=alert]")?.textContent).toBe("Die Anmeldung konnte nicht bestätigt werden. Bitte geben Sie Ihre Angaben ein.");
    expect(byLabel(container, "Name")).toBeTruthy();
    expect(byLabel(container, "E-Mail")).toBeTruthy();
    expect(posted).toContainEqual({ p: "tiqora-chat/1", type: "identity", verified: false });
    // and the token is gone: the retry goes out anonymously
    api.createSession.mockImplementationOnce(botCreate());
    await fillAndStart(container);
    expect((api.createSession.mock.calls[1][0] as { identity?: string }).identity).toBeUndefined();
  });

  it("identify on a running chat posts /identify and learns identified_as", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const identify = vi.fn(async () => undefined);
    const api = fakeApi({ identify, session: vi.fn(async () => session({ identified_as: "Erika Muster" })) });
    const { container, posted } = await mountHost({ api, store });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    await flush();
    expect(identify).toHaveBeenCalledWith("sid1", "tok1", TOKEN);
    expect(container.textContent).toContain("Angemeldet als Erika Muster");
    expect(posted).toContainEqual({ p: "tiqora-chat/1", type: "identity", verified: true });
  });

  it("identity_conflict on /identify clears the stored chat and starts fresh; invalid_identity drops the token", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const identify = vi.fn(async () => {
      throw new ChatApiError(409, "identity_conflict");
    });
    const { container, api } = await mountHost({ api: fakeApi({ identify }), store });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    await flush();
    expect(loadSession("wc_key", store)).toBeNull();
    expect(container.querySelector(".tq-log")).toBeNull();
    expect(container.textContent).toContain("Angemeldet"); // the new token still signs the new chat in
    api.createSession.mockImplementationOnce(botCreate());
    input(byLabel(container, "Ihre Nachricht"), "Neu");
    click(byText(container, "button", "Chat starten"));
    await flush();
    expect((api.createSession.mock.calls[0][0] as { identity?: string }).identity).toBe(TOKEN);

    const store2 = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store2);
    const bad = await mountHost({ api: fakeApi({ identify: vi.fn(async () => { throw new ChatApiError(422, "invalid_identity"); }) }), store: store2 });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    await flush();
    expect(bad.posted).toContainEqual({ p: "tiqora-chat/1", type: "identity", verified: false });
    expect(loadSession("wc_key", store2)).not.toBeNull(); // the anonymous chat goes on
  });

  it("logout clears the stored chat and the sign-in and shows a fresh start", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const { container, api } = await mountHost({ store });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    await flush();
    expect(container.querySelector(".tq-log")).toBeTruthy();
    toHost({ p: "tiqora-chat/1", type: "logout" });
    await flush();
    expect(loadSession("wc_key", store)).toBeNull();
    expect(container.querySelector(".tq-log")).toBeNull();
    expect(byLabel(container, "Name")).toBeTruthy(); // anonymous form again
    expect(api.end).not.toHaveBeenCalled(); // nothing is ended server-side
  });

  it("a stored identified chat is restored only if the host signs in before the visitor opens the chat", async () => {
    const mk = () => {
      const store = memStore();
      saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1, identified: true }, store);
      return store;
    };
    // host never identifies: dropped on open, nothing fetched
    const store = mk();
    const a = await mountHost({ store });
    expect(a.api.session).not.toHaveBeenCalled();
    toHost({ p: "tiqora-chat/1", type: "open" });
    await flush();
    expect(a.api.session).not.toHaveBeenCalled();
    expect(loadSession("wc_key", store)).toBeNull();
    expect(byLabel(a.container, "Name")).toBeTruthy();

    // host identifies after ready, before open: restored (and identified again)
    const store2 = mk();
    const identify = vi.fn(async () => undefined);
    const b = await mountHost({ store: store2, api: fakeApi({ identify, session: vi.fn(async () => session({ identified_as: "Erika Muster" })) }) });
    expect(b.container.querySelector(".tq-log")).toBeNull();
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    toHost({ p: "tiqora-chat/1", type: "open" });
    await flush();
    expect(b.container.querySelector(".tq-log")).toBeTruthy();
    expect(identify).toHaveBeenCalledWith("sid1", "tok1", TOKEN);
    expect(loadSession("wc_key", store2)).not.toBeNull();

    // an anonymous stored chat is restored as before
    const store3 = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store3);
    const c = await mountHost({ store: store3 });
    expect(c.container.querySelector(".tq-log")).toBeTruthy();
  });

  it("portal config fetches the portal identity once and uses it (also for the restore rule)", async () => {
    const portalIdentity = vi.fn(async (_b: Record<string, unknown>) => ({ identity: TOKEN, name: "Erika Muster" }));
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1, identified: true }, store);
    const identify = vi.fn(async () => undefined);
    const api = fakeApi({ config: vi.fn(async () => cfg({ portal_identity: true })), portalIdentity, identify, session: vi.fn(async () => session({ identified_as: "Erika Muster" })) });
    const { container } = await mountHost({ api, store, p: { origin: "http://localhost" } });
    expect(portalIdentity).toHaveBeenCalledTimes(1);
    expect(portalIdentity.mock.calls[0][0]).toMatchObject({ key: "wc_key", origin: "http://localhost" });
    expect(container.querySelector(".tq-log")).toBeTruthy(); // restored without a host identify
    expect(identify).toHaveBeenCalledWith("sid1", "tok1", TOKEN);

    // no chat yet: the form is the signed-in one
    const api2 = fakeApi({ config: vi.fn(async () => cfg({ portal_identity: true })), portalIdentity: vi.fn(async () => ({ identity: TOKEN, name: "Erika Muster" })) });
    const m = await mountHost({ api: api2, p: { origin: "http://localhost" } });
    expect(m.container.querySelector(".tq-signed")?.textContent).toBe("Angemeldet als Erika Muster");
    // a portal that is not logged in (404) stays anonymous
    const api3 = fakeApi({ config: vi.fn(async () => cfg({ portal_identity: true })), portalIdentity: vi.fn(async () => { throw new ChatApiError(404, "not_found"); }) });
    const n = await mountHost({ api: api3 });
    expect(byLabel(n.container, "Name")).toBeTruthy();
  });

  it("logout before the visitor opens the chat clears a deferred signed-in chat (I1)", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1, identified: true }, store);
    const { api } = await mountHost({ store });
    toHost({ p: "tiqora-chat/1", type: "logout" });
    await flush();
    expect(loadSession("wc_key", store)).toBeNull();
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    toHost({ p: "tiqora-chat/1", type: "open" });
    await flush();
    expect(api.session).not.toHaveBeenCalled(); // nobody restores A's chat for B
  });

  it("a restore checks ownership first: on identity_conflict no transcript is fetched or shown (I3)", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1, identified: true }, store);
    const identify = vi.fn(async () => {
      throw new ChatApiError(409, "identity_conflict");
    });
    const { container, api } = await mountHost({ store, api: fakeApi({ identify }) });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    toHost({ p: "tiqora-chat/1", type: "open" });
    await flush();
    expect(identify).toHaveBeenCalledWith("sid1", "tok1", TOKEN);
    expect(api.session).not.toHaveBeenCalled();
    expect(api.messages).not.toHaveBeenCalled();
    expect(container.querySelector(".tq-log")).toBeNull();
    expect(loadSession("wc_key", store)).toBeNull();
  });

  it("a portal assertion older than 9 minutes is fetched again before the chat starts (I2)", async () => {
    let n = 0;
    const portalIdentity = vi.fn(async (_b: Record<string, unknown>) => ({ identity: `tok.${++n}.sig`, name: "Erika Muster" }));
    const api = fakeApi({ config: vi.fn(async () => cfg({ portal_identity: true })), portalIdentity, createSession: botCreate() });
    const now = Date.now();
    const clock = vi.spyOn(Date, "now").mockReturnValue(now);
    const { container } = await mountHost({ api, p: { origin: "http://localhost" } });
    expect(portalIdentity).toHaveBeenCalledTimes(1);
    clock.mockReturnValue(now + 10 * 60 * 1000);
    toHost({ p: "tiqora-chat/1", type: "open" });
    await fillAndStart(container, false);
    clock.mockRestore();
    expect(portalIdentity).toHaveBeenCalledTimes(2);
    expect((api.createSession.mock.calls[0][0] as { identity?: string }).identity).toBe("tok.2.sig");
  });

  it("a signed-in chat is stored as identified even if the follow-up session read fails (M1)", async () => {
    let first = true;
    const api = fakeApi({
      createSession: botCreate(),
      session: vi.fn(async () => {
        if (first) {
          first = false;
          throw new ChatApiError(500, "boom");
        }
        return session();
      }),
    });
    const { container, store } = await mountHost({ api });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    toHost({ p: "tiqora-chat/1", type: "open" });
    await fillAndStart(container, false);
    expect(loadSession("wc_key", store)?.identified).toBe(true);
  });

  it("a signed-in visitor still sees fields the identity does not supply; a 422 on a hidden field shows an error (final I1)", async () => {
    const fields = [
      { key: "name", kind: "text" as const, label: "Name", required: true, options: [], identity_supplied: true },
      { key: "email", kind: "email" as const, label: "E-Mail", required: true, options: [], identity_supplied: true },
      { key: "billing", kind: "email" as const, label: "Rechnungs-E-Mail", required: true, options: [], identity_supplied: false },
    ];
    const createSession = vi.fn(async (_b: Record<string, unknown>) => {
      throw new ChatApiError(422, "invalid", { name: "required" });
    });
    const api = fakeApi({ config: vi.fn(async () => cfg({ prechat_fields: fields })), createSession });
    const { container } = await mountHost({ api });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    toHost({ p: "tiqora-chat/1", type: "open" });
    expect(() => byLabel(container, "Name")).toThrow();
    expect(() => byLabel(container, "E-Mail")).toThrow();
    input(byLabel(container, "Rechnungs-E-Mail"), "rechnung@example.org");
    await fillAndStart(container, false);
    expect((createSession.mock.calls[0][0] as { prechat: unknown }).prechat).toEqual({ billing: "rechnung@example.org" });
    // the error belongs to a field that is not on screen: it must not vanish silently
    expect(container.querySelector("[role=alert]")?.textContent).toBe("Etwas ist schiefgelaufen. Bitte versuchen Sie es noch einmal.");
  });

  it("logout then identify of the next visitor drops a restored anonymous chat before the sign-in (final I2)", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const identify = vi.fn(async () => undefined);
    const api = fakeApi({ identify, messages: vi.fn(async () => [msg({ seq: 1, sender: "visitor", body: "Frage von A" })]) });
    const { container } = await mountHost({ api, store });
    expect(container.textContent).toContain("Frage von A"); // restored before `ready`, anonymous
    // the loader flushes a queued logout() before the queued identify() of the next visitor
    toHost({ p: "tiqora-chat/1", type: "logout" });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    await flush();
    expect(identify).not.toHaveBeenCalled(); // B is never attached to A's chat
    expect(container.textContent).not.toContain("Frage von A");
    expect(loadSession("wc_key", store)).toBeNull();
    expect(container.querySelector(".tq-signed")?.textContent).toBe("Angemeldet"); // B's sign-in, fresh form
  });

  it("a verified identity without a name is reported as verified and shown as signed in (final M1)", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const identify = vi.fn(async () => undefined);
    const api = fakeApi({ identify, session: vi.fn(async () => session({ identified: true, identified_as: null })) });
    const { container, posted } = await mountHost({ api, store });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    await flush();
    expect(identify).toHaveBeenCalledWith("sid1", "tok1", TOKEN);
    expect(posted).toContainEqual({ p: "tiqora-chat/1", type: "identity", verified: true });
    expect(container.querySelector(".tq-chat .tq-signed")?.textContent).toBe("Angemeldet");
  });

  it("an ended signed-in chat is dropped from storage instead of being identified on every page view (final M6)", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1, identified: true }, store);
    const identify = vi.fn(async () => {
      throw new ChatApiError(409, "session_closed");
    });
    const { container, api } = await mountHost({ store, api: fakeApi({ identify }) });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    toHost({ p: "tiqora-chat/1", type: "open" });
    await flush();
    expect(identify).toHaveBeenCalledTimes(1);
    expect(api.session).not.toHaveBeenCalled();
    expect(api.messages).not.toHaveBeenCalled();
    expect(loadSession("wc_key", store)).toBeNull();
    expect(container.querySelector(".tq-signed")).toBeTruthy(); // the signed-in pre-chat form
  });

  it("a logout under offline_mode hide with nobody online leaves no empty panel (final M4)", async () => {
    const store = memStore();
    saveSession("wc_key", { sessionId: "sid1", token: "tok1", lastSeq: 1 }, store);
    const api = fakeApi({ config: vi.fn(async () => cfg({ online: false, offline_mode: "hide" })) });
    const { container } = await mountHost({ api, store });
    toHost({ p: "tiqora-chat/1", type: "open" });
    expect(container.querySelector(".tq-log")).toBeTruthy();
    toHost({ p: "tiqora-chat/1", type: "logout" });
    await flush();
    expect(container.querySelector(".tq-log")).toBeNull();
    expect(container.querySelector(".tq-notice")?.textContent).toBe("Gerade ist niemand im Chat");
  });

  it("preview frames never ask for the portal identity and ignore the host's sign-in", async () => {
    const portalIdentity = vi.fn(async () => ({ identity: TOKEN, name: "X" }));
    const api = fakeApi({ config: vi.fn(async () => cfg({ portal_identity: true })), portalIdentity });
    const { container } = await mountHost({ api, p: { preview: true } });
    toHost({ p: "tiqora-chat/1", type: "identify", token: TOKEN });
    expect(portalIdentity).not.toHaveBeenCalled();
    expect(byLabel(container, "Name")).toBeTruthy();
  });
});
