import type { ChatMessage, ChatPublicConfig } from "./api";

export function cfg(over: Partial<ChatPublicConfig> = {}): ChatPublicConfig {
  return {
    bot_name: "KI-Assistent",
    greeting: "Willkommen",
    max_message_length: 100,
    offline_mode: "form",
    online: true,
    prechat_fields: [
      { key: "name", kind: "text", label: "Name", required: true, options: [] },
      { key: "email", kind: "email", label: "E-Mail", required: true, options: [] },
    ],
    portal_identity: false,
    privacy_url: "https://example.org/privacy",
    theme: { mode: "auto", position: "right", primary_color: "#2563eb" },
    topics: [],
    upload: { enabled: true, max_bytes: 1024, types: ["image/png"] },
    ...over,
  };
}

export function msg(over: Partial<ChatMessage> & { seq: number }): ChatMessage {
  return {
    agent_name: null,
    attachments: [],
    body: "x",
    client_id: null,
    code: null,
    created: "2026-10-10T10:00:00Z",
    params: {},
    sender: "agent",
    ...over,
  };
}

import { act } from "preact/test-utils";

export const input = (el: Element, value: string) =>
  act(() => {
    (el as HTMLInputElement).value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
export const click = (el: Element) =>
  act(() => {
    (el as HTMLElement).click();
  });
export const keydown = (el: Element, key: string, init: KeyboardEventInit = {}) => {
  const ev = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  act(() => {
    el.dispatchEvent(ev);
  });
  return ev;
};
export const flush = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
export function byLabel(root: ParentNode, text: string): HTMLElement {
  const label = Array.from(root.querySelectorAll("label")).find((l) => l.textContent?.trim().startsWith(text));
  const target = label && (label.htmlFor ? root.querySelector(`#${label.htmlFor}`) : label.querySelector("input"));
  if (!target) throw new Error(`no field labelled ${text}`);
  return target as HTMLElement;
}
export function byText(root: ParentNode, selector: string, text: string): HTMLElement {
  const el = Array.from(root.querySelectorAll<HTMLElement>(selector)).find((e) => e.textContent?.includes(text));
  if (!el) throw new Error(`no ${selector} with ${text}`);
  return el;
}
