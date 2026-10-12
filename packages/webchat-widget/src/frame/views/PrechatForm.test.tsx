import { describe, expect, it, vi } from "vitest";
import { renderPreact } from "../../test/render";
import { loadMessages } from "../i18n";
import { byLabel, byText, cfg, click, input } from "../testkit";
import { PrechatForm } from "./PrechatForm";

async function setup(over = {}, serverErrors: Record<string, string> = {}, identity?: { name: string | null } | null) {
  const t = await loadMessages("de");
  const onSubmit = vi.fn();
  const { container } = renderPreact(
    <PrechatForm
      config={cfg(over)}
      t={t}
      title="Bevor es losgeht"
      submitLabel="Chat starten"
      topic={null}
      onTopic={() => undefined}
      busy={false}
      serverErrors={serverErrors}
      formError={null}
      onSubmit={onSubmit}
      identity={identity}
    />,
  );
  return { container, onSubmit };
}

describe("PrechatForm", () => {
  it("validates client side and associates the errors", async () => {
    const { container, onSubmit } = await setup();
    click(byText(container, "button", "Chat starten"));
    expect(onSubmit).not.toHaveBeenCalled();
    const name = byLabel(container, "Name");
    expect(name.getAttribute("aria-invalid")).toBe("true");
    expect(container.querySelector(`#${name.getAttribute("aria-describedby")}`)?.textContent).toBe("Bitte ausfüllen.");
    input(name, "Erika");
    input(byLabel(container, "E-Mail"), "kaputt");
    input(byLabel(container, "Ihre Nachricht"), "Hi");
    click(byText(container, "button", "Chat starten"));
    expect(container.textContent).toContain("Bitte eine gültige E-Mail-Adresse eingeben.");
    input(byLabel(container, "E-Mail"), "e@example.org");
    click(byText(container, "button", "Chat starten"));
    expect(onSubmit).toHaveBeenCalledWith({ prechat: { name: "Erika", email: "e@example.org" }, message: "Hi", topic: null });
  });

  it("shows server field errors", async () => {
    const { container } = await setup({}, { email: "email" });
    expect(byLabel(container, "E-Mail").getAttribute("aria-invalid")).toBe("true");
    expect(container.textContent).toContain("Bitte eine gültige E-Mail-Adresse eingeben.");
  });

  it("renders the privacy link safely and only offers online topics", async () => {
    const { container } = await setup({
      topics: [
        { key: "a", label: "A", online: true },
        { key: "b", label: "B", online: false },
      ],
      privacy_url: ["java", "script:alert(1)"].join(""),
    });
    expect(container.querySelector(".tq-privacy a")).toBeNull();
    const opts = Array.from(container.querySelectorAll<HTMLOptionElement>("#tq-f-topic option"));
    expect(opts.map((o) => o.disabled)).toEqual([false, true]);
  });

  it("an AI-first topic without a human stays disabled: the server refuses it under form/hide (I4)", async () => {
    const { container } = await setup({
      offline_mode: "form",
      topics: [
        { key: "a", label: "A", online: true },
        { key: "b", label: "B", online: false, ai_first: true },
      ],
    });
    const opts = Array.from(container.querySelectorAll<HTMLOptionElement>("#tq-f-topic option"));
    expect(opts.map((o) => o.disabled)).toEqual([false, true]);
  });

  it("links the privacy notice with noopener", async () => {
    const { container } = await setup();
    const a = container.querySelector(".tq-privacy a");
    expect(a?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(a?.getAttribute("href")).toBe("https://example.org/privacy");
  });

  it("a signed-in visitor sees no name/e-mail fields, is told who they are and submits without them", async () => {
    const { container, onSubmit } = await setup({}, {}, { name: "Erika Muster" });
    expect(() => byLabel(container, "Name")).toThrow();
    expect(() => byLabel(container, "E-Mail")).toThrow();
    expect(container.querySelector(".tq-signed")?.textContent).toBe("Angemeldet als Erika Muster");
    input(byLabel(container, "Ihre Nachricht"), "Hilfe");
    click(byText(container, "button", "Chat starten"));
    expect(onSubmit).toHaveBeenCalledWith({ prechat: {}, message: "Hilfe", topic: null });
  });

  it("a sign-in without a known name still says so", async () => {
    const { container } = await setup({}, {}, { name: null });
    expect(container.querySelector(".tq-signed")?.textContent).toBe("Angemeldet");
  });
});
