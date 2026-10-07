import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { RequireAuth } from "./RequireAuth";

let isAuthenticated = false;
let isLoading = false;
let pathname = "/agent/tickets/5";
let searchStr = "";

vi.mock("./AuthContext", () => ({
  useAuth: () => ({ isAuthenticated, isLoading }),
}));

const navigateMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  Navigate: (props: { to: string; search: unknown; replace: boolean }) => {
    navigateMock(props);
    return <div data-testid="navigate-stub">{props.to}</div>;
  },
  useRouterState: ({ select }: { select: (s: { location: { pathname: string; searchStr: string } }) => unknown }) =>
    select({ location: { pathname, searchStr } }),
}));

describe("RequireAuth", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    isAuthenticated = false;
    isLoading = false;
    pathname = "/agent/tickets/5";
    searchStr = "";
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
        search: { next: "/agent/tickets/5" },
        replace: true,
      }),
    );
    expect(screen.queryByTestId("protected")).toBeNull();
  });

  it("does not redirect again once the location is already /login", () => {
    pathname = "/login";
    searchStr = "?next=%2Fagent";
    render(
      <RequireAuth>
        <div data-testid="protected">secret</div>
      </RequireAuth>,
    );
    expect(navigateMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("protected")).toBeNull();
  });

  it("keeps the query string in next", () => {
    pathname = "/agent/dial";
    searchStr = "?number=%2B49171&ticket=42";
    render(
      <RequireAuth>
        <div data-testid="protected">secret</div>
      </RequireAuth>,
    );
    expect(navigateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        search: { next: "/agent/dial?number=%2B49171&ticket=42" },
      }),
    );
  });

  it("falls back to /agent as next when pathname is empty", () => {
    pathname = "";
    render(
      <RequireAuth>
        <div data-testid="protected">secret</div>
      </RequireAuth>,
    );
    expect(navigateMock).toHaveBeenCalledWith(
      expect.objectContaining({ search: { next: "/agent" } }),
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
