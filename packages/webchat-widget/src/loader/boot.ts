/**
 * Loader logic, one run per page. The loader is publicly cached for 5 minutes, so the
 * launcher must never show on its own: it appears only after the frame posted `ready`
 * with `hidden: false`, which the frame does only after a successful /config of an
 * enabled channel. No `ready` means no launcher, ever.
 */
import { PROTOCOL, TOPIC_KEY, type FrameToHost } from "../shared/protocol";
import { createHostBridge } from "./bridge";
import { createEvents, installHostApi } from "./hostApi";
import { LAUNCHER_Z, createLauncher, css, type Launcher } from "./launcher";

const LOADER_PATH = "/chat/v1/loader.js";
const KEY = /^[A-Za-z0-9_-]{1,40}$/;
const PANEL_ID = "tiqora-chat-panel";
const IDLE_MS = 1500;
const MOBILE = "(max-width: 480px)";

type Win = Window & { __tiqoraChatLoaded?: boolean };

export function boot(win: Win, script: HTMLScriptElement | null): void {
  const doc = win.document;
  const isLoaderSrc = (el: HTMLScriptElement) => {
    try {
      return new URL(el.src).pathname === LOADER_PATH;
    } catch {
      return false;
    }
  };
  // Without document.currentScript (module/eval-style embeds) the script is looked up
  // by its src; anything but exactly one match could be somebody else's tag: abort.
  let s = script;
  if (!s) {
    const found = [...doc.querySelectorAll<HTMLScriptElement>("script[data-tiqora-chat]")].filter(isLoaderSrc);
    s = found.length === 1 ? found[0] : null;
  }
  const key = s?.dataset.tiqoraChat ?? "";
  if (!s || !KEY.test(key) || !isLoaderSrc(s)) {
    console.warn("[tiqora-chat] no single valid loader script tag (data-tiqora-chat, src .../chat/v1/loader.js)");
    return;
  }
  let tiqoraOrigin: string;
  try {
    const u = new URL(s.src);
    if (u.protocol !== "https:" && u.protocol !== "http:") return;
    tiqoraOrigin = u.origin;
  } catch {
    return;
  }
  if (win.__tiqoraChatLoaded === true) {
    console.warn("[tiqora-chat] loader already running on this page, this embed is ignored");
    return; // double embed: the first one wins
  }
  win.__tiqoraChatLoaded = true;

  const events = createEvents();
  let ready = false;
  let hidden = false;
  let open = false;
  let wantOpen = false; // host API call before `ready`
  let topic: string | null = null;
  let launcher: Launcher | null = null;
  let panel: HTMLDivElement | null = null;
  let iframe: HTMLIFrameElement | null = null;
  let bridge: ReturnType<typeof createHostBridge> | null = null;
  let side: "right" | "left" = "right";

  const layout = () => {
    if (!panel) return;
    const full = !!win.matchMedia?.(MOBILE).matches;
    css(
      panel,
      full
        ? { top: "0", right: "0", bottom: "0", left: "0", width: "100%", height: "100%", "max-height": "none", "border-radius": "0" }
        : {
            top: "auto",
            right: side === "right" ? "20px" : "auto",
            left: side === "left" ? "20px" : "auto",
            bottom: "88px",
            width: "380px",
            height: "600px",
            "max-height": "calc(100vh - 100px)",
            "border-radius": "12px",
          },
    );
  };

  const setOpen = (o: boolean) => {
    if (!ready || hidden || !panel || !launcher || o === open) return;
    open = o;
    css(panel, { display: o ? "block" : "none" });
    launcher.setOpen(o);
    if (o) launcher.setUnread(0);
    bridge?.post({ p: PROTOCOL, type: o ? "open" : "close" });
    if (o) {
      iframe?.focus();
    } else {
      launcher.el.focus();
    }
    events.emit(o ? "open" : "close");
  };

  const onFrame = (m: FrameToHost) => {
    switch (m.type) {
      case "ready": {
        if (ready) return;
        ready = true;
        hidden = m.hidden;
        side = m.theme.position;
        events.emit("ready", { online: m.online });
        events.emit("availability", { online: m.online });
        if (hidden || !panel) return;
        launcher = createLauncher(doc, {
          label: m.launcherLabel || "Chat",
          position: m.theme.position,
          color: m.theme.primaryColor,
          controls: PANEL_ID,
          onClick: () => setOpen(!open),
        });
        doc.body.appendChild(launcher.el);
        layout();
        if (topic) bridge?.post({ p: PROTOCOL, type: "set-topic", topic });
        if (wantOpen) setOpen(true);
        return;
      }
      case "availability":
        events.emit("availability", { online: m.online });
        return;
      case "unread":
        launcher?.setUnread(open ? 0 : m.count);
        return;
      case "message":
        events.emit("message", { sender: m.sender });
        return;
      case "close-request":
        setOpen(false);
        return;
    }
  };

  installHostApi(win, {
    open() {
      wantOpen = true;
      setOpen(true);
    },
    close() {
      wantOpen = false;
      setOpen(false);
    },
    toggle() {
      if (ready ? open : wantOpen) this.close();
      else this.open();
    },
    setTopic(k) {
      if (!TOPIC_KEY.test(k)) return;
      topic = k;
      if (ready && !hidden) bridge?.post({ p: PROTOCOL, type: "set-topic", topic: k });
    },
    on: events.on,
  });

  const create = () => {
    const lang = (doc.documentElement.lang || win.navigator.language || "en").slice(0, 16);
    iframe = doc.createElement("iframe");
    iframe.title = "Chat";
    iframe.setAttribute("allow", "clipboard-write");
    iframe.referrerPolicy = "strict-origin";
    css(iframe, { width: "100%", height: "100%", border: "0", display: "block", background: "transparent" }, true);
    iframe.src =
      `${tiqoraOrigin}/chat/v1/frame?k=${encodeURIComponent(key)}` +
      `&origin=${encodeURIComponent(win.location.origin)}&lang=${encodeURIComponent(lang)}`;
    panel = doc.createElement("div");
    panel.id = PANEL_ID;
    css(panel, {
      display: "none",
      position: "fixed",
      overflow: "hidden",
      "z-index": String(Number(LAUNCHER_Z) + 1),
      background: "transparent",
      "box-shadow": "0 8px 30px rgba(0,0,0,.28)",
      "box-sizing": "border-box",
    }, true);
    layout();
    panel.appendChild(iframe);
    doc.body.appendChild(panel); // no wrapper: nothing for host rules like `body > div` to hit
    bridge = createHostBridge({ iframe, tiqoraOrigin, onFrameMessage: onFrame });
    const mq = win.matchMedia?.(MOBILE);
    if (mq?.addEventListener) mq.addEventListener("change", layout);
    else mq?.addListener?.(layout); // Safari < 14
  };

  const later = () => {
    const ric = (win as unknown as { requestIdleCallback?: (cb: () => void, o: { timeout: number }) => void })
      .requestIdleCallback;
    if (ric) ric.call(win, create, { timeout: IDLE_MS });
    else win.setTimeout(create, IDLE_MS);
  };
  if (doc.readyState === "complete") later();
  else win.addEventListener("load", later, { once: true });
}
