import { describe, expect, it, vi } from "vitest";
import { act } from "preact/test-utils";
import { renderPreact } from "../../test/render";
import { loadMessages } from "../i18n";
import { cfg, click, input, keydown } from "../testkit";
import { Composer } from "./Composer";

async function setup(over = {}) {
  const t = await loadMessages("de");
  const h = { onSend: vi.fn(), onFile: vi.fn(), onTyping: vi.fn() };
  const { container } = renderPreact(<Composer config={cfg(over)} t={t} disabled={false} {...h} />);
  const ta = container.querySelector("textarea") as HTMLTextAreaElement;
  return { container, ta, h };
}

const file = (name: string, type: string, size: number) => new File([new Uint8Array(size)], name, { type });
const pick = (container: HTMLElement, f: File) =>
  act(() => {
    const i = container.querySelector("input[type=file]") as HTMLInputElement;
    Object.defineProperty(i, "files", { value: [f], configurable: true });
    i.dispatchEvent(new Event("change", { bubbles: true }));
  });

describe("Composer", () => {
  it("Enter sends, Shift+Enter does not, too long is blocked", () => {
    return setup().then(({ container, ta, h }) => {
      input(ta, "Hallo");
      const shift = keydown(ta, "Enter", { shiftKey: true });
      expect(shift.defaultPrevented).toBe(false);
      expect(h.onSend).not.toHaveBeenCalled();
      const enter = keydown(ta, "Enter");
      expect(enter.defaultPrevented).toBe(true);
      expect(h.onSend).toHaveBeenCalledWith("Hallo");
      expect(ta.value).toBe("");

      input(ta, "x".repeat(101));
      expect(container.textContent).toContain("101 / 100"); // counter above 90 %
      keydown(ta, "Enter");
      expect(h.onSend).toHaveBeenCalledTimes(1);
      expect(container.querySelector("[role=alert]")?.textContent).toBe("Die Nachricht ist zu lang (höchstens 100 Zeichen).");
    });
  });

  it("shows no counter below 90 % and pings typing at most every 3 s", async () => {
    const { container, ta, h } = await setup();
    input(ta, "a");
    input(ta, "ab");
    expect(h.onTyping).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain("/ 100");
  });

  it("rejects too-large and wrong-type files before upload", async () => {
    const { container, h } = await setup();
    pick(container, file("big.png", "image/png", 2048));
    expect(container.querySelector("[role=alert]")?.textContent).toBe("Die Datei ist zu groß (höchstens 1 KB).");
    pick(container, file("a.svg", "image/svg+xml", 10));
    expect(container.querySelector("[role=alert]")?.textContent).toBe("Diese Dateiart wird nicht angenommen.");
    pick(container, file("empty.png", "image/png", 0));
    expect(container.querySelector("[role=alert]")?.textContent).toBe("Die Datei ist leer.");
    expect(h.onFile).not.toHaveBeenCalled();
    pick(container, file("ok.png", "image/png", 100));
    expect(h.onFile).toHaveBeenCalledTimes(1);
    expect(container.querySelector("[role=alert]")).toBeNull();
  });

  it("uploads a pasted image and the paperclip opens the file input", async () => {
    const { container, ta, h } = await setup();
    const f = file("p.png", "image/png", 10);
    const ev = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(ev, "clipboardData", { value: { files: [f] } });
    act(() => {
      ta.dispatchEvent(ev);
    });
    expect(h.onFile).toHaveBeenCalledWith(f);
    expect(container.querySelector("input[type=file]")?.getAttribute("accept")).toBe("image/png");
    const spy = vi.spyOn(container.querySelector("input[type=file]") as HTMLInputElement, "click");
    click(container.querySelector("button[aria-label='Datei anhängen']") as Element);
    expect(spy).toHaveBeenCalled();
  });

  it("hides the paperclip when uploads are off", async () => {
    const { container } = await setup({ upload: { enabled: false, max_bytes: 1, types: [] } });
    expect(container.querySelector("input[type=file]")).toBeNull();
  });
});
