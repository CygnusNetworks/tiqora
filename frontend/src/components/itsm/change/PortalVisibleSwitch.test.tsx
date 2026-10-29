import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { makeChange, renderWithClient } from "./changeTestSupport";
import { PortalVisibleSwitch } from "./PortalVisibleSwitch";

const { update } = vi.hoisted(() => ({ update: vi.fn() }));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, update } };
});

describe("PortalVisibleSwitch", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    update.mockReset();
  });
  afterEach(cleanup);

  it("shows the change in the portal's maintenance schedule when switched on", async () => {
    update.mockResolvedValue(makeChange({ portal_visible: true }));
    renderWithClient(<PortalVisibleSwitch change={makeChange({ portal_visible: false })} />);
    expect(screen.getByTestId("change-portal-visible")).toHaveTextContent("Im Kundenportal im Wartungskalender anzeigen");
    const toggle = screen.getByTestId("change-portal-visible-switch");
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(update).toHaveBeenCalledWith(130, { portal_visible: true }));
  });

  it("cannot be switched without write access or on a final change", () => {
    renderWithClient(<PortalVisibleSwitch change={makeChange({ can_write: false, portal_visible: true })} />);
    expect(screen.getByTestId("change-portal-visible-switch")).toBeDisabled();
    expect(screen.getByTestId("change-portal-visible-switch")).toBeChecked();
    cleanup();
    renderWithClient(<PortalVisibleSwitch change={makeChange({ phase: "closed" })} />);
    expect(screen.getByTestId("change-portal-visible-switch")).toBeDisabled();
  });
});
