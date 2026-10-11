/**
 * Host-page loader (placeholder, Task 14). Embedded as
 * `<script src="https://<tiqora>/chat/v1/loader.js" data-tiqora-chat="wc_…" async>`.
 * For now it only reads the public key from its own script tag; the launcher, iframe
 * and `window.Tiqora.chat` API follow in Task 16.
 */
const script = document.currentScript;
const publicKey = script instanceof HTMLScriptElement ? (script.dataset.tiqoraChat ?? "") : "";

if (!publicKey) {
  console.warn("[tiqora-chat] data-tiqora-chat attribute missing on the loader script");
}
