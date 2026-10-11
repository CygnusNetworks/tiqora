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
    expect(loadSession("wc_key", store)).toEqual({ sessionId: "sid1", token: "tok1", lastSeq: 1 });
    expect(stream.made[0].start).toHaveBeenCalled();
    expect(stream.made[0].o.token).toBe("tok1");

    stream.emit("session", { status: "active", agent_name: "Max" });
    stream.emit("message", msg({ seq: 2, sender: "agent", body: "Hallo!", agent_name: "Max" }), "2");
    expect(container.textContent).toContain("Max ist im Chat");
    expect(container.querySelector(".tq-other .tq-bubble")?.textContent).toBe("Hallo!");
    expect(post).toHaveBeenCalledWith({ p: "tiqora-chat/1", type: "message", sender: "agent" });
    expect(JSON.stringify(post.mock.calls)).not.toContain("Hallo!");

    // a bot (AI) message is shown like an agent message, and the host is told "agent"
    expect(post.mock.calls.filter((c) => c[0].type === "message")).toHaveLength(1);
    stream.emit("message", msg({ seq: 3, sender: "bot", body: "Ich bin die KI", agent_name: null }), "3");
    expect(container.textContent).toContain("Ich bin die KI");
    expect(post.mock.calls.filter((c) => c[0].type === "message")).toHaveLength(2);

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
});
