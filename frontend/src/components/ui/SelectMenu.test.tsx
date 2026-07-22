import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Menu, MenuItem } from "./Menu";
import { SelectMenu, type SelectMenuItem } from "./SelectMenu";

const FEW_ITEMS: SelectMenuItem<number>[] = [
  { value: 1, label: "Alpha" },
  { value: 2, label: "Beta", hint: "beta-login" },
  { value: 3, label: "Gamma" },
];

function manyItems(n: number): SelectMenuItem<number>[] {
  return Array.from({ length: n }, (_, i) => ({ value: i + 1, label: `Item ${i + 1}` }));
}

function renderSelectMenu(
  items: SelectMenuItem<number>[],
  { onSelect = vi.fn(), value = null as number | null } = {},
) {
  render(
    <div>
      <button data-testid="outside">outside</button>
      <SelectMenu
        items={items}
        value={value}
        onSelect={onSelect}
        panelTestId="sm"
        placeholder="Suche…"
        trigger={({ ref, toggleProps }) => (
          <button ref={ref} data-testid="trigger" {...toggleProps}>
            open
          </button>
        )}
      />
    </div>,
  );
  return { onSelect };
}

describe("SelectMenu", () => {
  it("renders its items, portaled onto document.body", () => {
    renderSelectMenu(FEW_ITEMS);
    fireEvent.click(screen.getByTestId("trigger"));
    const panel = screen.getByTestId("sm");
    expect(panel).toBeInTheDocument();
    expect(panel.parentElement).toBe(document.body);
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(panel).toHaveAttribute("data-portal-menu");
  });

  it("shows the search field only once item count exceeds the threshold", () => {
    renderSelectMenu(FEW_ITEMS);
    fireEvent.click(screen.getByTestId("trigger"));
    expect(screen.queryByTestId("sm-search")).toBeNull();
    fireEvent.click(screen.getByTestId("trigger"));

    renderSelectMenu(manyItems(9));
    fireEvent.click(screen.getAllByTestId("trigger")[1]);
    expect(screen.getByTestId("sm-search")).toBeInTheDocument();
  });

  it("filters items by label and hint via the search field", () => {
    renderSelectMenu(manyItems(9));
    fireEvent.click(screen.getByTestId("trigger"));
    fireEvent.change(screen.getByTestId("sm-search"), { target: { value: "item 3" } });
    expect(screen.getByText("Item 3")).toBeInTheDocument();
    expect(screen.queryByText("Item 1")).toBeNull();
  });

  it("navigates with ArrowDown/ArrowUp and selects with Enter", () => {
    const { onSelect } = renderSelectMenu(FEW_ITEMS);
    fireEvent.click(screen.getByTestId("trigger"));
    const panel = screen.getByTestId("sm");
    fireEvent.keyDown(panel, { key: "ArrowDown" });
    fireEvent.keyDown(panel, { key: "ArrowDown" });
    fireEvent.keyDown(panel, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith(3);
    expect(screen.queryByTestId("sm")).toBeNull();
  });

  it("calls onSelect and closes on item click", () => {
    const { onSelect } = renderSelectMenu(FEW_ITEMS);
    fireEvent.click(screen.getByTestId("trigger"));
    fireEvent.click(screen.getByText("Beta"));
    expect(onSelect).toHaveBeenCalledWith(2);
    expect(screen.queryByTestId("sm")).toBeNull();
  });

  it("closes on Escape and on outside pointer-down", () => {
    renderSelectMenu(FEW_ITEMS);
    fireEvent.click(screen.getByTestId("trigger"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("sm")).toBeNull();

    fireEvent.click(screen.getByTestId("trigger"));
    expect(screen.getByTestId("sm")).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByTestId("outside"));
    expect(screen.queryByTestId("sm")).toBeNull();
  });

  it("clicking inside a SelectMenu panel nested in a Menu does not close the outer Menu", () => {
    const onSelect = vi.fn();
    render(
      <Menu
        panelTestId="outer-menu"
        trigger={({ ref, toggleProps }) => (
          <button ref={ref} data-testid="outer-trigger" {...toggleProps}>
            open outer
          </button>
        )}
      >
        <MenuItem testId="outer-item">Outer item</MenuItem>
        <SelectMenu
          items={FEW_ITEMS}
          onSelect={onSelect}
          panelTestId="inner-select"
          trigger={({ ref, toggleProps }) => (
            <button ref={ref} data-testid="inner-trigger" {...toggleProps}>
              open inner
            </button>
          )}
        />
      </Menu>,
    );
    fireEvent.click(screen.getByTestId("outer-trigger"));
    fireEvent.click(screen.getByTestId("inner-trigger"));
    expect(screen.getByTestId("inner-select")).toBeInTheDocument();

    // A pointerdown on the portaled panel bubbles to `document` — the outer
    // Menu's outside-pointerdown handler must ignore it (data-portal-menu).
    fireEvent.pointerDown(screen.getByText("Alpha"));
    expect(screen.getByTestId("outer-menu")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Alpha"));
    expect(onSelect).toHaveBeenCalledWith(1);
    expect(screen.getByTestId("outer-menu")).toBeInTheDocument();
  });
});
