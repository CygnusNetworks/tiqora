import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AccountMenu } from "./AccountMenu";

const { logout, navigate, setTheme, authUser } = vi.hoisted(() => ({
  logout: vi.fn().mockResolvedValue(undefined),
  navigate: vi.fn(),
  setTheme: vi.fn(),
  authUser: {
    id: 7,
    login: "jdoe",
    first_name: "Jane",
    last_name: "Doe",
    email: "jane@example.com",
    is_admin: false as boolean,
  },
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: authUser,
    logout,
  }),
}));

vi.mock("@/themes/theme", () => ({
  useTheme: () => ({ theme: "dark", setTheme }),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

function open() {
  render(
    <I18nextProvider i18n={i18n}>
      <AccountMenu />
    </I18nextProvider>,
  );
  fireEvent.click(screen.getByTestId("account-menu-trigger"));
}

describe("AccountMenu", () => {
  beforeEach(() => {
    logout.mockClear();
    navigate.mockClear();
    setTheme.mockClear();
    authUser.is_admin = false;
  });

  it("shows the signed-in identity and the core actions", () => {
    open();
    expect(screen.getByTestId("account-menu-name")).toHaveTextContent("Jane Doe");
    expect(screen.getByTestId("current-user")).toHaveTextContent("Jane Doe");
    expect(screen.queryByTestId("account-menu-settings")).not.toBeInTheDocument();
    expect(screen.queryByTestId("account-menu-admin")).not.toBeInTheDocument();
    expect(screen.getByTestId("account-menu-security")).toBeInTheDocument();
    // Language is a portal-based SelectMenu trigger (scales to many; renders
    // above the menu instead of being clipped by its overflow-hidden panel).
    const langTrigger = screen.getByTestId("account-menu-lang-select");
    expect(langTrigger).toBeInTheDocument();
    fireEvent.click(langTrigger);
    const langPanel = screen.getByTestId("account-menu-lang-panel");
    expect(within(langPanel).getByText("Deutsch")).toBeInTheDocument();
    expect(within(langPanel).getByText("English")).toBeInTheDocument();
    fireEvent.click(langTrigger);
    expect(screen.getByTestId("account-menu-theme-light")).toBeInTheDocument();
    expect(screen.getByTestId("logout-btn")).toBeInTheDocument();
  });

  it("shows a highlighted Admin-Bereich entry only for is_admin users", () => {
    authUser.is_admin = true;
    open();
    const adminBtn = screen.getByTestId("account-menu-admin");
    expect(adminBtn).toBeInTheDocument();
    expect(adminBtn).toHaveTextContent(/Admin/i);
    // Highlighted via accent fill (MenuItem highlight prop).
    expect(adminBtn.className).toMatch(/bg-accent/);
    fireEvent.click(adminBtn);
    expect(navigate).toHaveBeenCalledWith({ to: "/admin" });
  });

  it("does not render a general Einstellungen / settings entry", () => {
    open();
    expect(screen.queryByTestId("account-menu-settings")).toBeNull();
    // German locale default — security label only, not the old settings string as a menu item.
    expect(screen.queryByText("Einstellungen")).not.toBeInTheDocument();
  });

  it("navigates to security / 2FA settings", () => {
    open();
    fireEvent.click(screen.getByTestId("account-menu-security"));
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/security" });
  });

  it("changes language via the SelectMenu and persists the choice, keeping the menu open", () => {
    const changeLanguage = vi.spyOn(i18n, "changeLanguage");
    open();
    fireEvent.click(screen.getByTestId("account-menu-lang-select"));
    fireEvent.click(within(screen.getByTestId("account-menu-lang-panel")).getByText("English"));
    expect(changeLanguage).toHaveBeenCalledWith("en");
    expect(localStorage.getItem("tiqora-lang")).toBe("en");
    // The surrounding account Menu stays open — SelectMenu's portal panel is
    // recognized as "inside" by Menu.tsx's outside-pointerdown handler.
    expect(screen.getByTestId("account-menu")).toBeInTheDocument();
    changeLanguage.mockRestore();
  });

  it("toggles theme via setTheme", () => {
    open();
    fireEvent.click(screen.getByTestId("account-menu-theme-light"));
    expect(setTheme).toHaveBeenCalledWith("light");
  });

  it("fires logout from the sign-out item", () => {
    open();
    fireEvent.click(screen.getByTestId("logout-btn"));
    expect(logout).toHaveBeenCalledOnce();
  });

  it("renders an avatar for the signed-in user (Gravatar when email is set)", () => {
    open();
    const img = screen.getByTestId("account-menu-avatar");
    expect(img.tagName).toBe("IMG");
    expect(img).toHaveAttribute(
      "src",
      expect.stringMatching(/^https:\/\/www\.gravatar\.com\/avatar\/[0-9a-f]{32}\?/),
    );
  });
});
