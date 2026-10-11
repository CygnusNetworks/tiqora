import { beforeAll, beforeEach, expect, test, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { EditToolbar, type EditToolbarProps } from "./EditToolbar";

const AT_10_42 = Date.parse("2026-10-10T08:42:00Z"); // Europe/Berlin

function bar(over: Partial<EditToolbarProps> = {}) {
  const props: EditToolbarProps = {
    name: "Mein Start",
    draftSavedAt: null,
    restoreAt: null,
    onRestore: vi.fn(),
    onDropDraft: vi.fn(),
    canUndo: false,
    canRedo: false,
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    onDiscard: vi.fn(),
    onSave: vi.fn(),
    saving: false,
    error: null,
    ...over,
  };
  render(<EditToolbar {...props} />);
  return props;
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => setDisplayTimeZone("Europe/Berlin"));

test("names the dashboard, uses the inverse tokens and is a labelled toolbar", () => {
  bar();
  const toolbar = screen.getByRole("toolbar", { name: "Bearbeiten" });
  expect(toolbar).toHaveTextContent("Mein Start bearbeiten");
  expect(toolbar).toHaveClass("bg-ink", "text-surface", "sticky");
  expect(screen.queryByText(/Entwurf gesichert/)).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("shows the draft time in the display zone once a draft was written", () => {
  bar({ draftSavedAt: AT_10_42 });
  expect(screen.getByText("Entwurf gesichert · 10:42")).toBeVisible();
});

test("undo/redo follow the history; the buttons call back", () => {
  const p = bar({ canUndo: true });
  expect(screen.getByRole("button", { name: "Rückgängig" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Wiederholen" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Rückgängig" }));
  fireEvent.click(screen.getByRole("button", { name: "Verwerfen" }));
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  expect(p.onUndo).toHaveBeenCalledTimes(1);
  expect(p.onDiscard).toHaveBeenCalledTimes(1);
  expect(p.onSave).toHaveBeenCalledTimes(1);
});

test("offers an earlier draft for restore or to be dropped", () => {
  const p = bar({ restoreAt: AT_10_42 });
  fireEvent.click(screen.getByRole("button", { name: "Entwurf von 10:42 wiederherstellen" }));
  fireEvent.click(screen.getByRole("button", { name: "Entwurf verwerfen" }));
  expect(p.onRestore).toHaveBeenCalledTimes(1);
  expect(p.onDropDraft).toHaveBeenCalledTimes(1);
});

test("while saving both final buttons are disabled; an error is announced", () => {
  bar({ saving: true, error: "Das Layout ist zu hoch." });
  expect(screen.getByRole("button", { name: "Wird gespeichert …" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Verwerfen" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("Das Layout ist zu hoch.");
});
