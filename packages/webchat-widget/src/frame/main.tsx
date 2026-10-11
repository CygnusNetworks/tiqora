/** Chat frame entry: reads the query parameters the loader/admin preview passed and renders the app. */
import { render } from "preact";
import { App, type FrameParams } from "./App";
import { createChatApi } from "./api";
import { isRtl, pickLanguage } from "./i18n";
import { LANGS } from "virtual:webchat-locales";

/** Exact http(s) origin (no path, no credentials) or null. */
function parseOrigin(v: string | null): string | null {
  if (!v) return null;
  try {
    const u = new URL(v);
    return (u.protocol === "https:" || u.protocol === "http:") && u.origin === v ? u.origin : null;
  } catch {
    return null;
  }
}

function start(): void {
  const q = new URLSearchParams(window.location.search);
  const lang = pickLanguage(q.get("lang") ?? navigator.language, LANGS);
  document.documentElement.lang = lang.replace("_", "-");
  document.documentElement.dir = isRtl(lang) ? "rtl" : "ltr";
  const k = q.get("k") ?? "";
  const origin = parseOrigin(q.get("origin"));
  const root = document.getElementById("app");
  if (!root || !k || !origin) return; // a frame without a valid host origin stays empty and never posts
  const preview = q.get("preview") === "1";
  if (preview && origin !== window.location.origin) return; // preview is Tiqora-own only
  const params: FrameParams = { k, origin, lang, preview };
  render(<App params={params} api={createChatApi()} />, root);
}

start();
