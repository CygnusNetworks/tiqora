import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { RequirePortalAuth } from "./RequirePortalAuth";

let isAuthenticated = false;
let isLoading = false;
let pathname = "/portal/tickets/5";

vi.mock("./CustomerAuthContext", () => ({
  useCustomerAuth: () => ({ isAuthenticated, isLoading }),
}));

const navigateMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  Navigate: (props: { to: string; search: unknown; replace: boolean }) => {
    navigateMock(props);
    return <div data-testid="navigate-stub">{props.to}</div>;
  },
  useRouterState: ({ select }: { select: (s: { location: { pathname: string } } ) => unknown }) =>
    select({ location: { pathname } }),
}));

describe("RequirePortalAuth", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    isAuthenticated = false;
    isLoading = false;
    pathname = "/portal/tickets/5";
  });

  it("shows a spinner while loading", () => {
    isLoading = true;
    render(
      <RequirePortalAuth>
        <div data-testid="protected">secret</div>
      </RequirePortalAuth>,
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByTestId("protected")).toBeNull();
  });

  it("redirects to /portal/login with the current path when unauthenticated", () => {
    render(
      <RequirePortalAuth>
        <div data-testid="protected">secret</div>
      </RequirePortalAuth>,
    );
    expect(screen.getByTestId("navigate-stub")).toHaveTextContent("/portal/login");
    expect(navigateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        to: "/portal/login",
        search: { next: encodeURIComponent(pathname) },
        replace: true,
      }),
    );
    expect(screen.queryByTestId("protected")).toBeNull();
  });

  it("falls back to /portal as next when pathname is empty", () => {
    pathname = "";
    render(
      <RequirePortalAuth>
        <div data-testid="protected">secret</div>
      </RequirePortalAuth>,
    );
    expect(navigateMock).toHaveBeenCalledWith(
      expect.objectContaining({ search: { next: encodeURIComponent("/portal") } }),
    );
  });

  it("renders children when authenticated", () => {
    isAuthenticated = true;
    render(
      <RequirePortalAuth>
        <div data-testid="protected">secret</div>
      </RequirePortalAuth>,
    );
    expect(screen.getByTestId("protected")).toBeInTheDocument();
    expect(screen.queryByTestId("navigate-stub")).toBeNull();
  });
});
