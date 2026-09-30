import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { RefineDiffView } from "./RefineDiffView";

const BEFORE = "eins zwei drei vier fünf";
const AFTER = "EINS zwei DREI vier FÜNF";

function setup(overrides: Partial<Parameters<typeof RefineDiffView>[0]> = {}) {
  const onAccept = vi.fn();
  const onDiscard = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <RefineDiffView
        before={BEFORE}
        after={AFTER}
        toneLabel="Förmlich"
        onAccept={onAccept}
        onDiscard={onDiscard}
        {...overrides}
      />
    </I18nextProvider>,
  );
  return { onAccept, onDiscard };
}

describe("RefineDiffView", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });

  it("renders del/ins marks and the header", () => {
    setup();
    const root = screen.getByTestId("refine-review");
    expect(root.querySelectorAll("del")).toHaveLength(3);
    expect(root.querySelectorAll("ins")).toHaveLength(3);
    expect(root).toHaveTextContent("✦ Verfeinert · Förmlich");
    expect(root).toHaveTextContent("3 Änderungen");
  });

  it("toggles a change on click and updates the count", () => {
    setup();
    fireEvent.click(screen.getAllByTestId("refine-review-change")[0]);
    expect(screen.getByTestId("refine-review")).toHaveTextContent("2 von 3 Änderungen aktiv");
    expect(screen.getByTestId("refine-review").querySelectorAll("ins")).toHaveLength(2);
  });

  it("accepts the current selection", () => {
    const { onAccept } = setup();
    fireEvent.click(screen.getAllByTestId("refine-review-change")[0]);
    fireEvent.click(screen.getByTestId("refine-review-accept"));
    expect(onAccept).toHaveBeenCalledWith("eins zwei DREI vier FÜNF", { total: 3, on: 2 });
  });

  it("discards", () => {
    const { onDiscard, onAccept } = setup();
    fireEvent.click(screen.getByTestId("refine-review-discard"));
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(onAccept).not.toHaveBeenCalled();
  });

  it("shows two labelled columns in side-by-side view", () => {
    setup();
    fireEvent.click(screen.getByTestId("refine-review-view-side"));
    const root = screen.getByTestId("refine-review");
    expect(within(root).getByText("Vorher")).toBeInTheDocument();
    expect(within(root).getByText("Nachher")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("refine-review-view-inline"));
    expect(within(root).queryByText("Vorher")).toBeNull();
  });

  it("moves the focus ring with prev/next", () => {
    setup();
    const changes = () => screen.getAllByTestId("refine-review-change");
    fireEvent.click(screen.getByTestId("refine-review-next"));
    expect(changes()[0]).toHaveAttribute("aria-current", "true");
    fireEvent.click(screen.getByTestId("refine-review-next"));
    expect(changes()[1]).toHaveAttribute("aria-current", "true");
    expect(changes()[0]).not.toHaveAttribute("aria-current");
    fireEvent.click(screen.getByTestId("refine-review-prev"));
    expect(changes()[0]).toHaveAttribute("aria-current", "true");
    fireEvent.click(screen.getByTestId("refine-review-prev"));
    expect(changes()[2]).toHaveAttribute("aria-current", "true");
  });

  it("changes are keyboard operable", () => {
    setup();
    const first = screen.getAllByTestId("refine-review-change")[0];
    expect(first).toHaveAttribute("role", "button");
    fireEvent.keyDown(first, { key: "Enter" });
    expect(screen.getByTestId("refine-review")).toHaveTextContent("2 von 3");
    fireEvent.keyDown(screen.getAllByTestId("refine-review-change")[0], { key: " " });
    expect(screen.getByTestId("refine-review")).toHaveTextContent("3 Änderungen");
  });

  it("mutes quote lines and marks nothing inside them", () => {
    const q = "\n\n> Am 29.09. schrieb X:\n> Hallo";
    setup({ before: "hallo welt" + q, after: "Hallo Welt" + q });
    const root = screen.getByTestId("refine-review");
    const muted = root.querySelectorAll("span.text-muted");
    expect(Array.from(muted).map((m) => m.textContent).join("")).toContain("> Hallo");
    expect(within(root).getAllByTestId("refine-review-change")).toHaveLength(1);
  });

  it("says so when nothing changed", () => {
    setup({ before: "a b", after: "a b" });
    expect(screen.getByTestId("refine-review")).toHaveTextContent("Keine Änderungen vorgeschlagen");
  });
});
