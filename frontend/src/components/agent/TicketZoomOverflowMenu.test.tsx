import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { TicketZoomOverflowMenu } from "./TicketZoomOverflowMenu";

function wrap(
  props: Partial<React.ComponentProps<typeof TicketZoomOverflowMenu>> = {},
) {
  const defaults = {
    tab: "articles" as const,
    onTabChange: vi.fn(),
    sortLabel: "Newest first",
    onToggleSort: vi.fn(),
    onInternalNote: vi.fn(),
    canStartProcess: true,
    onStartProcess: vi.fn(),
  };
  return {
    ...defaults,
    ...props,
    result: render(
      <I18nextProvider i18n={i18n}>
        <TicketZoomOverflowMenu {...defaults} {...props} />
      </I18nextProvider>,
    ),
  };
}

describe("TicketZoomOverflowMenu", () => {
  it("renders the ⋮ trigger and opens the action menu", () => {
    wrap();
    expect(screen.getByTestId("ticket-zoom-overflow-trigger")).toBeInTheDocument();
    expect(screen.queryByTestId("ticket-zoom-overflow-menu")).toBeNull();
    fireEvent.click(screen.getByTestId("ticket-zoom-overflow-trigger"));
    expect(screen.getByTestId("ticket-zoom-overflow-menu")).toBeInTheDocument();
    expect(screen.getByTestId("overflow-tab-articles")).toBeInTheDocument();
    expect(screen.getByTestId("overflow-tab-history")).toBeInTheDocument();
    expect(screen.getByTestId("overflow-sort")).toBeInTheDocument();
    expect(screen.getByTestId("overflow-internal-note")).toBeInTheDocument();
    expect(screen.getByTestId("overflow-start-process")).toBeInTheDocument();
  });

  it("fires callbacks for tab, sort, note, and process actions", () => {
    const onTabChange = vi.fn();
    const onToggleSort = vi.fn();
    const onInternalNote = vi.fn();
    const onStartProcess = vi.fn();
    wrap({ onTabChange, onToggleSort, onInternalNote, onStartProcess });
    fireEvent.click(screen.getByTestId("ticket-zoom-overflow-trigger"));
    fireEvent.click(screen.getByTestId("overflow-tab-history"));
    expect(onTabChange).toHaveBeenCalledWith("history");

    fireEvent.click(screen.getByTestId("ticket-zoom-overflow-trigger"));
    fireEvent.click(screen.getByTestId("overflow-sort"));
    expect(onToggleSort).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByTestId("ticket-zoom-overflow-trigger"));
    fireEvent.click(screen.getByTestId("overflow-internal-note"));
    expect(onInternalNote).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByTestId("ticket-zoom-overflow-trigger"));
    fireEvent.click(screen.getByTestId("overflow-start-process"));
    expect(onStartProcess).toHaveBeenCalledOnce();
  });

  it("hides start-process when the ticket is already in a process", () => {
    wrap({ canStartProcess: false });
    fireEvent.click(screen.getByTestId("ticket-zoom-overflow-trigger"));
    expect(screen.queryByTestId("overflow-start-process")).toBeNull();
  });
});
