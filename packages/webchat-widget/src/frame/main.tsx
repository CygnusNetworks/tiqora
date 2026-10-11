/**
 * Chat frame entry (placeholder, Task 14): loads the visitor's language and renders
 * "…". The real views follow in Task 15.
 */
import { render } from "preact";
import { isRtl, loadMessages, pickLanguage } from "./i18n";
import { LANGS } from "virtual:webchat-locales";

function Placeholder() {
  return <div aria-busy="true">…</div>;
}

async function start(): Promise<void> {
  const params = new URLSearchParams(window.location.search);
  const lang = pickLanguage(params.get("lang") ?? navigator.language, LANGS);
  document.documentElement.lang = lang.replace("_", "-");
  document.documentElement.dir = isRtl(lang) ? "rtl" : "ltr";
  await loadMessages(lang).catch(() => undefined);
  const root = document.getElementById("app");
  if (root) render(<Placeholder />, root);
}

void start();
