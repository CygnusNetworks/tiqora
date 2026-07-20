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
});
