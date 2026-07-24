import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { HomeRedirect } from "./HomeRedirect";

let isAuthenticated = false;
let isLoading = false;

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ isAuthenticated, isLoading }),
}));

const navigateMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  Navigate: ({ to }: { to: string }) => {
    navigateMock(to);
    return <div data-testid="navigate-stub">{to}</div>;
  },
  Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

function renderPage() {
  return render(
    <I18nextProvider i18n={i18n}>
      <HomeRedirect />
    </I18nextProvider>,
  );
}

describe("HomeRedirect", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    isAuthenticated = false;
    isLoading = false;
  });

  it("shows a spinner while auth state is loading", () => {
    isLoading = true;
    renderPage();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByTestId("navigate-stub")).toBeNull();
  });

  it("redirects to /agent when authenticated", () => {
    isAuthenticated = true;
    renderPage();
    expect(screen.getByTestId("navigate-stub")).toHaveTextContent("/agent");
    expect(navigateMock).toHaveBeenCalledWith("/agent");
  });

  it("shows the landing page with login and portal links when unauthenticated", () => {
    renderPage();
    expect(screen.queryByTestId("navigate-stub")).toBeNull();
    const loginLink = screen.getByRole("link", { name: i18n.t("auth.login") });
    expect(loginLink).toHaveAttribute("href", "/login");
    const portalLink = screen.getByRole("link", { name: i18n.t("nav.portal") });
    expect(portalLink).toHaveAttribute("href", "/portal");
  });
});
