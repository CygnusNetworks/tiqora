import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { RequireAdmin } from "./RequireAdmin";

let isAuthenticated = false;
let isLoading = false;
let user: { is_admin: boolean } | null = null;
let pathname = "/admin/users";

vi.mock("./AuthContext", () => ({
  useAuth: () => ({ isAuthenticated, isLoading, user }),
}));

vi.mock("@tanstack/react-router", () => ({
  Navigate: (props: { to: string }) => <div data-testid="navigate-stub">{props.to}</div>,
  useRouterState: ({ select }: { select: (s: { location: { pathname: string } }) => unknown }) =>
    select({ location: { pathname } }),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}));

function renderIt() {
  return render(
    <I18nextProvider i18n={i18n}>
      <RequireAdmin>
        <div data-testid="protected">admin only</div>
      </RequireAdmin>
    </I18nextProvider>,
  );
}

describe("RequireAdmin", () => {
  beforeEach(() => {
    isAuthenticated = false;
    isLoading = false;
    user = null;
    pathname = "/admin/users";
  });

  it("shows a spinner while the auth session is loading", () => {
    isLoading = true;
    renderIt();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByTestId("protected")).toBeNull();
  });

  it("redirects to /login when unauthenticated (RequireAuth gate)", () => {
    renderIt();
    expect(screen.getByTestId("navigate-stub")).toHaveTextContent("/login");
    expect(screen.queryByTestId("protected")).toBeNull();
  });

  it("shows access-denied UI for an authenticated non-admin user", () => {
    isAuthenticated = true;
    user = { is_admin: false };
    renderIt();
    expect(screen.getByTestId("admin-access-denied")).toBeInTheDocument();
    expect(screen.queryByTestId("protected")).toBeNull();
  });

  it("shows access-denied UI when user is null despite isAuthenticated (defensive)", () => {
    isAuthenticated = true;
    user = null;
    renderIt();
    expect(screen.getByTestId("admin-access-denied")).toBeInTheDocument();
  });

  it("renders children for an authenticated admin user", () => {
    isAuthenticated = true;
    user = { is_admin: true };
    renderIt();
    expect(screen.getByTestId("protected")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-access-denied")).toBeNull();
  });
});
