/**
 * Host-page loader. Embedded as
 * `<script src="https://<tiqora>/chat/v1/loader.js" data-tiqora-chat="wc_…" async>`.
 * It adds `window.Tiqora.chat`, then (after the page loaded) an iframe to the chat frame
 * and, once the frame reports `ready`, the launcher. Logic: boot.ts.
 */
import { boot } from "./boot";

boot(window, document.currentScript instanceof HTMLScriptElement ? document.currentScript : null);
