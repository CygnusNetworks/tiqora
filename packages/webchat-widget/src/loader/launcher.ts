/**
 * The floating launcher button. It lives on a third-party page under that page's CSP, so
 * it is built from DOM nodes and styled through the CSSOM only: no HTML strings, no
 * style/class attributes. `!important` keeps host CSS like `button { ... }` out.
 */
import { HEX_COLOR } from "../shared/protocol";

export type Launcher = {
  el: HTMLButtonElement;
  setOpen(open: boolean): void;
  setUnread(n: number): void;
  remove(): void;
};

export const LAUNCHER_Z = "2147483000";
const SVG_NS = "http://www.w3.org/2000/svg";
const ICON_CHAT = "M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H10l-5 4v-4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z";
const ICON_CLOSE = "M6 6l12 12M18 6L6 18";

/**
 * Host CSS isolation: `all: initial` first (an inline !important declaration beats the
 * host's author rules, !important or not, for every property), then the explicit values.
 * `all` also resets SVG geometry where the browser treats it as CSS (`d` in Chromium and
 * Firefox), so a reset `<path>` must set `d` again (see `iconPath`).
 */
export function css(el: HTMLElement | SVGElement, props: Record<string, string>, reset = false): void {
  if (reset) el.style.setProperty("all", "initial", "important");
  for (const [k, v] of Object.entries(props)) el.style.setProperty(k, v, "important");
}

/** A launcher icon path: reset like everything else, then its geometry and paint explicitly. */
function iconPath(doc: Document, d: string, props: Record<string, string>): SVGPathElement {
  const p = doc.createElementNS(SVG_NS, "path");
  p.setAttribute("d", d); // WebKit draws from the attribute (no CSS `d` there)
  css(p, { d: `path("${d}")`, opacity: "1", visibility: "visible", ...props }, true);
  return p;
}

export function createLauncher(
  doc: Document,
  opts: { label: string; position: "right" | "left"; color: string; onClick: () => void; controls?: string },
): Launcher {
  const el = doc.createElement("button");
  el.type = "button";
  el.setAttribute("aria-label", opts.label);
  el.title = opts.label;
  el.setAttribute("aria-expanded", "false");
  if (opts.controls) el.setAttribute("aria-controls", opts.controls);
  css(el, {
    position: "fixed",
    bottom: "20px",
    [opts.position]: "20px",
    width: "56px",
    height: "56px",
    "border-radius": "50%",
    border: "0",
    padding: "0",
    margin: "0",
    display: "flex",
    "align-items": "center",
    "justify-content": "center",
    cursor: "pointer",
    color: "#fff",
    "background-color": HEX_COLOR.test(opts.color) ? opts.color : "#2563eb",
    "box-shadow": "0 4px 14px rgba(0,0,0,.3)",
    "z-index": LAUNCHER_Z,
    "box-sizing": "border-box",
    "font-family": "system-ui,sans-serif",
  }, true);

  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  css(svg, { display: "block", width: "26px", height: "26px", overflow: "visible", color: "#fff" }, true);
  const chat = iconPath(doc, ICON_CHAT, { display: "inline", fill: "#fff", stroke: "none" });
  const close = iconPath(doc, ICON_CLOSE, {
    display: "none",
    fill: "none",
    stroke: "#fff",
    "stroke-width": "2.5px",
    "stroke-linecap": "round",
  });
  svg.append(chat, close);

  const badge = doc.createElement("span");
  badge.setAttribute("aria-hidden", "true");
  css(badge, {
    display: "none",
    position: "absolute",
    top: "-4px",
    [opts.position === "left" ? "right" : "left"]: "-4px",
    "min-width": "20px",
    height: "20px",
    padding: "0 5px",
    "box-sizing": "border-box",
    "border-radius": "10px",
    background: "#dc2626",
    color: "#fff",
    font: "700 12px/20px system-ui,sans-serif",
    "text-align": "center",
  }, true);
  el.append(svg, badge);
  el.addEventListener("click", () => opts.onClick());

  let open = false;
  let unread = 0;
  const paintBadge = () => {
    // The badge only matters while the panel is closed; text is always a text node.
    const text = unread > 99 ? "99+" : String(unread);
    const shown = unread > 0 && !open;
    badge.textContent = text;
    css(badge, { display: shown ? "block" : "none" });
    // The badge is aria-hidden: the count goes into the accessible name (no i18n in the loader).
    el.setAttribute("aria-label", shown ? `${opts.label} (${text})` : opts.label);
  };
  return {
    el,
    setOpen(o) {
      open = o;
      el.setAttribute("aria-expanded", String(o));
      css(chat, { display: o ? "none" : "inline" });
      css(close, { display: o ? "inline" : "none" });
      paintBadge();
    },
    setUnread(n) {
      unread = Number.isFinite(n) ? Math.max(0, Math.min(9999, Math.trunc(n))) : 0;
      paintBadge();
    },
    remove: () => el.remove(),
  };
}
