import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PROTOCOL } from "../shared/protocol";
import { boot } from "./boot";
import { createHostBridge } from "./bridge";
import { installHostApi, type HostApiImpl } from "./hostApi";
import { createLauncher } from "./launcher";

const ORIGIN = "https://tiqora.example";
const theme = { primaryColor: "#ff0000", position: "right", mode: "auto" } as const;
const ready = (over: Record<string, unknown> = {}) => ({
  p: PROTOCOL,
  type: "ready",
  online: true,
  hidden: false,
  launcherLabel: "Fragen?",
  theme,
  dir: "ltr",
  ...over,
});

function frameIframe(): HTMLIFrameElement {
  const iframe = document.createElement("iframe");
  document.body.appendChild(iframe);
  return iframe;
}

function send(data: unknown, origin: string, source: MessageEventSource | null) {
  window.dispatchEvent(new MessageEvent("message", { data, origin, source }));
}

afterEach(() => {
  document.body.replaceChildren();
  delete (window as unknown as Record<string, unknown>).Tiqora;
  delete (window as unknown as Record<string, unknown>).__tiqoraChatLoaded;
  vi.useRealTimers();
});

describe("host bridge", () => {
  it("ignores messages from other origins or windows", () => {
    const iframe = frameIframe();
    const onFrameMessage = vi.fn();
    const bridge = createHostBridge({ iframe, tiqoraOrigin: ORIGIN, onFrameMessage });
    const msg = { p: PROTOCOL, type: "unread", count: 3 };
    send(msg, "https://evil.example", iframe.contentWindow);
    send(msg, ORIGIN, window); // right origin, wrong window
    send(msg, ORIGIN, null);
    send({ p: PROTOCOL, type: "unread", count: -1 }, ORIGIN, iframe.contentWindow); // malformed
    send({ p: PROTOCOL, type: "unread", count: 3, token: "x" }, ORIGIN, iframe.contentWindow);
    expect(onFrameMessage).not.toHaveBeenCalled();
    send(msg, ORIGIN, iframe.contentWindow);
    expect(onFrameMessage).toHaveBeenCalledTimes(1);
    bridge.dispose();
    send(msg, ORIGIN, iframe.contentWindow);
    expect(onFrameMessage).toHaveBeenCalledTimes(1);
  });

  it("a detached iframe never matches a null source", () => {
    const iframe = document.createElement("iframe");
    const onFrameMessage = vi.fn();
    createHostBridge({ iframe, tiqoraOrigin: ORIGIN, onFrameMessage });
    send({ p: PROTOCOL, type: "unread", count: 1 }, ORIGIN, null);
    expect(onFrameMessage).not.toHaveBeenCalled();
  });

  it("posts only to the exact Tiqora origin", () => {
    const iframe = frameIframe();
    const spy = vi.spyOn(iframe.contentWindow!, "postMessage");
    createHostBridge({ iframe, tiqoraOrigin: ORIGIN, onFrameMessage: vi.fn() }).post({ p: PROTOCOL, type: "open" });
    expect(spy).toHaveBeenCalledWith({ p: PROTOCOL, type: "open" }, ORIGIN);
  });
});

describe("launcher", () => {
  it("is an accessible button styled through CSSOM", () => {
    const onClick = vi.fn();
    const l = createLauncher(document, { label: "Chat öffnen", position: "left", color: "#ff0000", onClick });
    expect(l.el.tagName).toBe("BUTTON");
    expect(l.el.type).toBe("button");
    expect(l.el.getAttribute("aria-label")).toBe("Chat öffnen");
    expect(l.el.getAttribute("style")).not.toContain("javascript");
    expect(l.el.style.left).toBe("20px");
    expect(l.el.style.zIndex).toBe("2147483000");
    expect(l.el.style.width).toBe("56px");
    l.setUnread(2);
    expect(l.el.textContent).toContain("2");
    expect(l.el.getAttribute("aria-label")).toBe("Chat öffnen (2)"); // the badge is aria-hidden
    l.setOpen(true);
    expect(l.el.getAttribute("aria-expanded")).toBe("true");
    expect(l.el.getAttribute("aria-label")).toBe("Chat öffnen");
    l.el.click();
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("badge text is a text node, never markup", () => {
    const l = createLauncher(document, { label: "x", position: "right", color: "red;background:url(x)", onClick: vi.fn() });
    l.setUnread(150);
    expect(l.el.textContent).toBe("99+");
    expect(l.el.querySelector("span")?.children.length).toBe(0);
    expect(l.el.style.backgroundColor).not.toContain("url");
  });
});

describe("host API", () => {
  const impl = (): HostApiImpl => ({ open: vi.fn(), close: vi.fn(), toggle: vi.fn(), setTopic: vi.fn(), on: vi.fn(() => () => undefined) });

  it("does not clobber window.Tiqora", () => {
    (window as unknown as { Tiqora: unknown }).Tiqora = { other: 1 };
    installHostApi(window, impl());
    const t = (window as unknown as { Tiqora: { other: number; chat: Record<string, unknown> } }).Tiqora;
    expect(t.other).toBe(1);
    expect(typeof t.chat.open).toBe("function");
    expect(Object.keys(t.chat).sort()).toEqual(["close", "on", "open", "setTopic", "toggle"]);
  });

  it("survives a frozen window.Tiqora", () => {
    (window as unknown as { Tiqora: unknown }).Tiqora = Object.freeze({ other: 1 });
    installHostApi(window, impl());
    const t = (window as unknown as { Tiqora: { other: number; chat: unknown } }).Tiqora;
    expect(t.other).toBe(1);
    expect(t.chat).toBeTruthy();
  });
});

describe("boot", () => {
  type Chat = { open(): void; close(): void; toggle(): void; setTopic(k: string): void; on(e: string, cb: (d: unknown) => void): () => void };
  const chat = () => (window as unknown as { Tiqora: { chat: Chat } }).Tiqora.chat;
  let script: HTMLScriptElement;

  beforeEach(() => {
    vi.useFakeTimers();
    script = document.createElement("script");
    script.src = `${ORIGIN}/chat/v1/loader.js`;
    script.dataset.tiqoraChat = "wc_key1";
    Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
  });

  const start = () => {
    boot(window, script);
    vi.advanceTimersByTime(1600);
    const iframe = document.querySelector("iframe") as HTMLIFrameElement;
    return iframe;
  };
  const launcher = () => document.querySelector<HTMLButtonElement>("button[aria-controls]");

  it("embeds the frame with the exact origin and keeps the launcher hidden until ready", () => {
    document.documentElement.lang = "de";
    const iframe = start();
    const url = new URL(iframe.src);
    expect(url.origin).toBe(ORIGIN);
    expect(url.pathname).toBe("/chat/v1/frame");
    expect(url.searchParams.get("k")).toBe("wc_key1");
    expect(url.searchParams.get("origin")).toBe(window.location.origin);
    expect(url.searchParams.get("lang")).toBe("de");
    expect(iframe.title).toBe("Chat");
    expect(iframe.getAttribute("allow")).toBe("clipboard-write");
    expect(iframe.referrerPolicy).toBe("strict-origin");
    expect(launcher()).toBeNull();
    send(ready(), "https://evil.example", iframe.contentWindow);
    expect(launcher()).toBeNull();
    send(ready(), ORIGIN, iframe.contentWindow);
    expect(launcher()?.getAttribute("aria-label")).toBe("Fragen?");
  });

  it("shows no launcher when the frame says hidden", () => {
    const iframe = start();
    send(ready({ hidden: true }), ORIGIN, iframe.contentWindow);
    expect(launcher()).toBeNull();
    chat().open(); // ignored safely
    expect(document.getElementById("tiqora-chat-panel")?.style.display).toBe("none");
  });

  it("shows no launcher when no ready ever arrives", () => {
    start();
    vi.advanceTimersByTime(60_000);
    expect(launcher()).toBeNull();
  });

  it("loads once per page", () => {
    start();
    boot(window, script);
    vi.advanceTimersByTime(2000);
    expect(document.querySelectorAll("iframe").length).toBe(1);
  });

  it("queues open and topic until ready, then opens", () => {
    boot(window, script);
    chat().open();
    chat().setTopic("sales");
    chat().setTopic("Not A Key!"); // invalid: dropped
    vi.advanceTimersByTime(1600);
    const iframe = document.querySelector("iframe") as HTMLIFrameElement;
    const post = vi.spyOn(iframe.contentWindow!, "postMessage");
    send(ready(), ORIGIN, iframe.contentWindow);
    expect(post).toHaveBeenCalledWith({ p: PROTOCOL, type: "set-topic", topic: "sales" }, ORIGIN);
    expect(post).toHaveBeenCalledWith({ p: PROTOCOL, type: "open" }, ORIGIN);
    expect((post.mock.calls as unknown[][]).every((c) => c[1] === ORIGIN)).toBe(true);
    expect(document.getElementById("tiqora-chat-panel")?.style.display).toBe("block");
    expect(launcher()?.getAttribute("aria-expanded")).toBe("true");
  });

  it("fires host events without message content and replays ready", () => {
    boot(window, script);
    const got: unknown[] = [];
    chat().on("message", (d) => got.push(d));
    chat().on("availability", (d) => got.push(["availability", d]));
    chat().on("open", () => got.push("open"));
    vi.advanceTimersByTime(1600);
    const iframe = document.querySelector("iframe") as HTMLIFrameElement;
    send(ready({ online: false }), ORIGIN, iframe.contentWindow);
    send({ p: PROTOCOL, type: "message", sender: "agent" }, ORIGIN, iframe.contentWindow);
    send({ p: PROTOCOL, type: "message", sender: "agent", body: "secret" }, ORIGIN, iframe.contentWindow);
    launcher()!.click();
    expect(got).toEqual([["availability", { online: false }], { sender: "agent" }, "open"]);
    const late = vi.fn();
    chat().on("ready", late);
    vi.advanceTimersByTime(10);
    expect(late).toHaveBeenCalledWith({ online: false });
  });

  it("a throwing host callback does not break the loader", () => {
    boot(window, script);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    chat().on("open", () => {
      throw new Error("host bug");
    });
    vi.advanceTimersByTime(1600);
    const iframe = document.querySelector("iframe") as HTMLIFrameElement;
    send(ready(), ORIGIN, iframe.contentWindow);
    chat().open();
    expect(launcher()?.getAttribute("aria-expanded")).toBe("true");
  });

  it("unread badge follows the frame and clears on open; close-request closes", () => {
    const iframe = start();
    send(ready(), ORIGIN, iframe.contentWindow);
    send({ p: PROTOCOL, type: "unread", count: 3 }, ORIGIN, iframe.contentWindow);
    expect(launcher()?.textContent).toContain("3");
    chat().toggle();
    expect(document.getElementById("tiqora-chat-panel")?.style.display).toBe("block");
    send({ p: PROTOCOL, type: "close-request" }, ORIGIN, iframe.contentWindow);
    expect(document.getElementById("tiqora-chat-panel")?.style.display).toBe("none");
    expect(document.activeElement).toBe(launcher());
  });

  const tag = (src: string, key: string) => {
    const el = document.createElement("script");
    el.src = src;
    el.dataset.tiqoraChat = key;
    document.head.appendChild(el);
    return el;
  };

  it("without currentScript, uses the fallback for exactly one loader tag", () => {
    tag("https://evil.example/other.js", "wc_planted"); // not a loader URL: ignored
    tag(`${ORIGIN}/chat/v1/loader.js?v=2`, "wc_fallback");
    boot(window, null);
    vi.advanceTimersByTime(2000);
    const url = new URL((document.querySelector("iframe") as HTMLIFrameElement).src);
    expect(url.origin).toBe(ORIGIN);
    expect(url.searchParams.get("k")).toBe("wc_fallback");
    document.head.replaceChildren();
  });

  it("without currentScript, two loader tags abort with a warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    tag(`${ORIGIN}/chat/v1/loader.js`, "wc_a");
    tag("https://evil.example/chat/v1/loader.js", "wc_b");
    boot(window, null);
    vi.advanceTimersByTime(2000);
    expect(document.querySelector("iframe")).toBeNull();
    expect(warn).toHaveBeenCalled();
    document.head.replaceChildren();
  });

  it("a non-loader src is refused even when handed in directly", () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    script.src = "https://evil.example/x.js";
    boot(window, script);
    vi.advanceTimersByTime(2000);
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("refuses a loader script without a valid key", () => {
    script.dataset.tiqoraChat = "bad key";
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    boot(window, script);
    vi.advanceTimersByTime(2000);
    expect(document.querySelector("iframe")).toBeNull();
  });
});
