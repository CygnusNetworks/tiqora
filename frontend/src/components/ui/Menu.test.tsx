import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Menu, MenuItem } from "./Menu";

function renderMenu(onSelect = vi.fn()) {
  render(
    <div>
      <button data-testid="outside">outside</button>
      <Menu
        panelTestId="menu"
        trigger={({ ref, toggleProps }) => (
          <button ref={ref} data-testid="trigger" {...toggleProps}>
            open
          </button>
        )}
      >
        <MenuItem testId="item" onSelect={onSelect}>
          Item
        </MenuItem>
      </Menu>
    </div>,
  );
  return { onSelect };
}

describe("Menu", () => {
  it("opens and closes on trigger click", () => {
    renderMenu();
    expect(screen.queryByTestId("menu")).toBeNull();
    fireEvent.click(screen.getByTestId("trigger"));
    expect(screen.getByTestId("menu")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("trigger"));
    expect(screen.queryByTestId("menu")).toBeNull();
  });

  it("closes on Escape", () => {
    renderMenu();
    fireEvent.click(screen.getByTestId("trigger"));
    expect(screen.getByTestId("menu")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("menu")).toBeNull();
  });

  it("closes on outside pointer-down", () => {
    renderMenu();
    fireEvent.click(screen.getByTestId("trigger"));
    expect(screen.getByTestId("menu")).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByTestId("outside"));
    expect(screen.queryByTestId("menu")).toBeNull();
  });

  it("fires the item's onSelect and closes by default", () => {
    const { onSelect } = renderMenu();
    fireEvent.click(screen.getByTestId("trigger"));
    fireEvent.click(screen.getByTestId("item"));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("menu")).toBeNull();
  });

  it("keeps the panel open for keepOpen items", () => {
    const onSelect = vi.fn();
    render(
      <Menu
        panelTestId="menu"
        trigger={({ ref, toggleProps }) => (
          <button ref={ref} data-testid="trigger" {...toggleProps}>
            open
          </button>
        )}
      >
        <MenuItem testId="item" keepOpen onSelect={onSelect}>
          Item
        </MenuItem>
      </Menu>,
    );
    fireEvent.click(screen.getByTestId("trigger"));
    fireEvent.click(screen.getByTestId("item"));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(screen.getByTestId("menu")).toBeInTheDocument();
  });

  it("does not close when a nested [data-portal-menu] panel is scrolled", () => {
    // Account language SelectMenu is portaled next to the Menu panel on body.
    renderMenu();
    fireEvent.click(screen.getByTestId("trigger"));
    const nested = document.createElement("div");
    nested.setAttribute("data-portal-menu", "");
    nested.setAttribute("data-testid", "nested-portal");
    document.body.appendChild(nested);
    try {
      fireEvent.scroll(nested);
      expect(screen.getByTestId("menu")).toBeInTheDocument();
    } finally {
      nested.remove();
    }
  });

  it("still closes when the page scroll moved the trigger", () => {
    renderMenu();
    const trigger = screen.getByTestId("trigger");
    fireEvent.click(trigger);
    vi.spyOn(trigger, "getBoundingClientRect").mockReturnValue(
      new DOMRect(0, -40, 32, 32),
    );
    fireEvent.scroll(document.body);
    expect(screen.queryByTestId("menu")).toBeNull();
  });

  it("stays open for a late scroll event when the trigger did not move", () => {
    // Scroll events arrive a frame late: a scroll-into-view right before the
    // click used to close the freshly opened menu (flaky process e2e).
    renderMenu();
    fireEvent.click(screen.getByTestId("trigger"));
    fireEvent.scroll(document.body);
    expect(screen.getByTestId("menu")).toBeInTheDocument();
  });

  it("opened with the keyboard, the first item takes the focus", () => {
    renderMenu();
    screen.getByTestId("trigger").focus();
    fireEvent.keyDown(screen.getByTestId("trigger"), { key: "Enter" });
    expect(screen.getByTestId("item")).toHaveFocus();
  });

  it("a disabled item is skipped by the arrows; a soft-disabled one stays focusable but does nothing", () => {
    const onSelect = vi.fn();
    render(
      <Menu
        panelTestId="menu"
        trigger={({ ref, toggleProps }) => (
          <button ref={ref} data-testid="trigger" {...toggleProps}>
            open
          </button>
        )}
      >
        <MenuItem testId="a">A</MenuItem>
        <MenuItem testId="b" disabled>
          B
        </MenuItem>
        <MenuItem testId="c" softDisabled title="Nicht möglich" onSelect={onSelect}>
          C
        </MenuItem>
      </Menu>,
    );
    screen.getByTestId("trigger").focus();
    fireEvent.keyDown(screen.getByTestId("trigger"), { key: "Enter" });
    fireEvent.keyDown(screen.getByTestId("a"), { key: "ArrowDown" });
    expect(screen.getByTestId("c")).toHaveFocus();
    expect(screen.getByTestId("c")).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByTestId("c"));
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByTestId("menu")).toBeInTheDocument();
  });
});
