import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { registerCoreWidgets } from "../widgets";
import { GalleryPanel } from "./GalleryPanel";
import { WIDGET_DRAG_TYPE } from "./galleryModel";

const auth = vi.hoisted(() => ({ user: { id: 5, is_admin: false, can_view_reports: false } }));
vi.mock("@/auth/AuthContext", async () => {
  const actual = await vi.importActual<typeof import("@/auth/AuthContext")>("@/auth/AuthContext");
  return { ...actual, useOptionalAuth: () => auth };
});

const onAdd = vi.fn();
const onClose = vi.fn();
function panel(widgetCount = 3) {
  return render(<GalleryPanel widgetCount={widgetCount} onAdd={onAdd} onClose={onClose} />);
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  onAdd.mockClear();
  onClose.mockClear();
  registerCoreWidgets();
});

describe("GalleryPanel", () => {
  it("renders title, search placeholder, group headings and the foot hint of the mockup", () => {
    panel();
    expect(screen.getByRole("complementary", { name: "Widget hinzufügen" })).toBeVisible();
    expect(screen.getByPlaceholderText("z. B. Eskalation, Notiz, Queue")).toBeVisible();
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Tickets", "Für mich", "Team"]);
    expect(screen.getByText("Ins Raster ziehen oder anklicken – das Widget landet in der ersten freien Lücke.")).toBeVisible();
  });

  it("a card is a real button in the tab order (Enter and Space activate it natively); clicking adds the widget in the first free spot", () => {
    panel();
    const card = screen.getByRole("button", { name: /Ticketliste/ });
    expect(card.tagName).toBe("BUTTON");
    expect(card).toHaveAttribute("type", "button");
    expect(card).not.toHaveAttribute("tabindex", "-1");
    fireEvent.click(card);
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ key: "core.ticket-list" }), undefined);
    fireEvent.click(screen.getByRole("button", { name: /Kalender/ }));
    expect(onAdd).toHaveBeenLastCalledWith(expect.objectContaining({ key: "core.calendar-agenda" }), undefined);
  });

  it("a card starts a drag that carries the widget key", () => {
    panel();
    const data = new Map<string, string>();
    const dataTransfer = { setData: (k: string, v: string) => data.set(k, v), effectAllowed: "" };
    fireEvent.dragStart(screen.getByRole("button", { name: /Kennzahl/ }).closest("li")!, { dataTransfer });
    expect(data.get(WIDGET_DRAG_TYPE)).toBe("core.counter");
    expect(dataTransfer.effectAllowed).toBe("copy");
  });

  it("at 40 widgets the cards are disabled, not draggable, and the panel says why", () => {
    panel(40);
    expect(screen.getByText("Es sind höchstens 40 Widgets möglich.")).toBeVisible();
    const card = screen.getByRole("button", { name: /Ticketliste/ });
    expect(card).toBeDisabled();
    expect(card.closest("li")).toHaveAttribute("draggable", "false");
  });

  it("an empty search result says 'Nichts gefunden' and offers to clear the search", () => {
    panel();
    fireEvent.change(screen.getByLabelText("Suchen"), { target: { value: "zzz" } });
    expect(screen.getByText("Nichts gefunden")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Suche löschen" }));
    expect(screen.getByLabelText("Suchen")).toHaveValue("");
    expect(screen.getByRole("button", { name: /Ticketliste/ })).toBeVisible();
  });

  it("offers every phase-1 core widget to an ordinary agent", () => {
    panel();
    expect(screen.getAllByRole("listitem")).toHaveLength(10);
  });

  it("Escape and the close button call onClose", () => {
    panel();
    fireEvent.keyDown(screen.getByLabelText("Suchen"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Galerie schließen" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("when the 40th widget disables the focused card, focus moves to the panel's close button", () => {
    const { rerender } = render(<GalleryPanel widgetCount={39} onAdd={onAdd} onClose={onClose} />);
    const card = screen.getByRole("button", { name: /Ticketliste/ });
    card.focus();
    expect(card).toHaveFocus();
    rerender(<GalleryPanel widgetCount={40} onAdd={onAdd} onClose={onClose} />);
    expect(screen.getByRole("button", { name: "Galerie schließen" })).toHaveFocus();
  });
});
