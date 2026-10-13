import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RouterAnchor } from "./RouterAnchor";

const { router } = vi.hoisted(() => ({ router: { current: null as null | { navigate: ReturnType<typeof vi.fn> } } }));
vi.mock("@tanstack/react-router", () => ({ useRouter: () => router.current }));

describe("RouterAnchor", () => {
  beforeEach(() => {
    router.current = { navigate: vi.fn() };
  });

  it("routes a plain left click through the router instead of a page load", () => {
    render(<RouterAnchor href="/agent/itsm/cis/znuny/7" data-testid="a">CI</RouterAnchor>);
    const a = screen.getByTestId("a");
    expect(a.getAttribute("href")).toBe("/agent/itsm/cis/znuny/7");
    const click = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    a.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    expect(router.current?.navigate).toHaveBeenCalledWith({ to: "/agent/itsm/cis/znuny/7" });
  });

  it("leaves modifier clicks and new-tab links to the browser", () => {
    render(
      <>
        <RouterAnchor href="/agent/tickets/1" data-testid="a">x</RouterAnchor>
        <RouterAnchor href="/agent/tickets/2" target="_blank" data-testid="b">y</RouterAnchor>
      </>,
    );
    fireEvent.click(screen.getByTestId("a"), { ctrlKey: true });
    fireEvent.click(screen.getByTestId("b"));
    expect(router.current?.navigate).not.toHaveBeenCalled();
  });

  it("is a plain link outside a router", () => {
    router.current = null;
    render(<RouterAnchor href="/agent/tickets/1" data-testid="a">x</RouterAnchor>);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    screen.getByTestId("a").dispatchEvent(click);
    expect(click.defaultPrevented).toBe(false);
  });
});
