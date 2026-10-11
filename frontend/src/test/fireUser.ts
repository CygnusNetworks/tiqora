import { fireEvent } from "@testing-library/react";

// @testing-library/user-event is not a dependency: a small stand-in over fireEvent.
const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
export function makeUser() {
  return {
    async click(el: Element) {
      fireEvent.click(el);
    },
    async clear(el: Element) {
      fireEvent.change(el, { target: { value: "" } });
    },
    async type(el: Element, text: string) {
      (el as HTMLElement).focus();
      for (const ch of text) fireEvent.change(el, { target: { value: (el as HTMLInputElement).value + ch } });
    },
    async selectOptions(el: Element, label: string) {
      const option = Array.from((el as HTMLSelectElement).options).find((o) => o.text === label || o.value === label);
      if (!option) throw new Error(`no option ${label}`);
      fireEvent.change(el, { target: { value: option.value } });
    },
    async keyboard(keys: string) {
      const key = keys.replace(/[{}]/g, "");
      fireEvent.keyDown(document.activeElement ?? document.body, { key });
    },
    async tab(opts: { shift?: boolean } = {}) {
      const from = document.activeElement ?? document.body;
      const notPrevented = fireEvent.keyDown(from, { key: "Tab", shiftKey: opts.shift === true });
      if (!notPrevented) return;
      const all = Array.from(document.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((e) => !e.closest("[inert]"));
      const i = all.indexOf(from as HTMLElement);
      all[(i + (opts.shift ? -1 : 1) + all.length) % all.length]?.focus();
    },
  };
}
