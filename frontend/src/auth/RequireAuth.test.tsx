import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { RequireAuth } from "./RequireAuth";

let isAuthenticated = false;
let isLoading = false;
let pathname = "/agent/tickets/5";

vi.mock("./AuthContext", () => ({
  useAuth: () => ({ isAuthenticated, isLoading }),
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

describe("RequireAuth", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    isAuthenticated = false;
    isLoading = false;
    pathname = "/agent/tickets/5";
  });

  it("shows a spinner while loading", () => {
    isLoading = true;
    render(
      <RequireAuth>
        <div data-testid="protected">secret</div>
      </RequireAuth>,
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByTestId("protected")).toBeNull();
  });

  it("redirects to /login with the current path when unauthenticated", () => {
    render(
      <RequireAuth>
        <div data-testid="protected">secret</div>
      </RequireAuth>,
    );
    expect(screen.getByTestId("navigate-stub")).toHaveTextContent("/login");
    expect(navigateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        to: "/login",
        search: { next: encodeURIComponent(pathname) },
        replace: true,
      }),
    );
    expect(screen.queryByTestId("protected")).toBeNull();
  });

  it("falls back to /agent as next when pathname is empty", () => {
    pathname = "";
    render(
      <RequireAuth>
        <div data-testid="protected">secret</div>
      </RequireAuth>,
    );
    expect(navigateMock).toHaveBeenCalledWith(
      expect.objectContaining({ search: { next: encodeURIComponent("/agent") } }),
    );
  });

  it("renders children when authenticated", () => {
    isAuthenticated = true;
    render(
      <RequireAuth>
        <div data-testid="protected">secret</div>
      </RequireAuth>,
    );
    expect(screen.getByTestId("protected")).toBeInTheDocument();
    expect(screen.queryByTestId("navigate-stub")).toBeNull();
  });
});
