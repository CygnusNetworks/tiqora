import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Popover } from "./Popover";
import { usePopoverClose } from "./popoverContext";

function CloseButton() {
  const close = usePopoverClose();
  return (
    <button type="button" data-testid="self-close" onClick={close}>
      done
    </button>
  );
}

function wrap(children: React.ReactNode = <input data-testid="field" />) {
  return render(
    <Popover
      label="Details"
      panelTestId="pop-panel"
      trigger={({ ref, toggleProps }) => (
        <button ref={ref} type="button" data-testid="pop-trigger" {...toggleProps}>
          open
        </button>
      )}
    >
      {children}
    </Popover>,
  );
}

describe("Popover", () => {
  it("stays closed until the trigger is clicked", () => {
    wrap();
    expect(screen.queryByTestId("pop-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("pop-trigger")).toHaveAttribute("aria-expanded", "false");
  });

  it("opens a labelled dialog panel and focuses the first control", async () => {
    wrap();
    fireEvent.click(screen.getByTestId("pop-trigger"));
    const panel = await screen.findByTestId("pop-panel");
    expect(panel).toHaveAttribute("role", "dialog");
    expect(panel).toHaveAttribute("aria-label", "Details");
    await waitFor(() => expect(screen.getByTestId("field")).toHaveFocus());
  });

  it("toggles shut on a second trigger click", async () => {
    wrap();
    fireEvent.click(screen.getByTestId("pop-trigger"));
    await screen.findByTestId("pop-panel");
    fireEvent.click(screen.getByTestId("pop-trigger"));
    expect(screen.queryByTestId("pop-panel")).not.toBeInTheDocument();
  });

  it("closes on Escape and returns focus to the trigger", async () => {
    wrap();
    fireEvent.click(screen.getByTestId("pop-trigger"));
    await screen.findByTestId("pop-panel");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("pop-panel")).not.toBeInTheDocument());
    expect(screen.getByTestId("pop-trigger")).toHaveFocus();
  });

  it("closes on an outside pointer-down but not on one inside the panel", async () => {
    wrap();
    fireEvent.click(screen.getByTestId("pop-trigger"));
    const panel = await screen.findByTestId("pop-panel");
    fireEvent.pointerDown(panel);
    expect(screen.getByTestId("pop-panel")).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    await waitFor(() => expect(screen.queryByTestId("pop-panel")).not.toBeInTheDocument());
  });

  it("ignores a pointer-down in a nested portal menu", async () => {
    wrap(
      <div>
        <input data-testid="field" />
        <div data-portal-menu data-testid="nested" />
      </div>,
    );
    fireEvent.click(screen.getByTestId("pop-trigger"));
    await screen.findByTestId("pop-panel");
    fireEvent.pointerDown(screen.getByTestId("nested"));
    expect(screen.getByTestId("pop-panel")).toBeInTheDocument();
  });

  it("lets panel content dismiss itself", async () => {
    wrap(<CloseButton />);
    fireEvent.click(screen.getByTestId("pop-trigger"));
    fireEvent.click(await screen.findByTestId("self-close"));
    await waitFor(() => expect(screen.queryByTestId("pop-panel")).not.toBeInTheDocument());
  });

  it("closes when the page scrolls out from under it", async () => {
    wrap();
    fireEvent.click(screen.getByTestId("pop-trigger"));
    await screen.findByTestId("pop-panel");
    fireEvent.scroll(document.body);
    await waitFor(() => expect(screen.queryByTestId("pop-panel")).not.toBeInTheDocument());
  });

  it("renders the panel outside the trigger's subtree so it cannot be clipped", async () => {
    const { container } = wrap();
    fireEvent.click(screen.getByTestId("pop-trigger"));
    const panel = await screen.findByTestId("pop-panel");
    expect(container.contains(panel)).toBe(false);
    expect(document.body.contains(panel)).toBe(true);
  });

  it("does not fetch focus when there is nothing focusable", async () => {
    const onError = vi.fn();
    window.addEventListener("error", onError);
    wrap(<p>nur Text</p>);
    fireEvent.click(screen.getByTestId("pop-trigger"));
    await screen.findByTestId("pop-panel");
    expect(onError).not.toHaveBeenCalled();
    window.removeEventListener("error", onError);
  });
});
