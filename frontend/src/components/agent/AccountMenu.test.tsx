import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AccountMenu } from "./AccountMenu";

const { logout, navigate, setTheme } = vi.hoisted(() => ({
  logout: vi.fn().mockResolvedValue(undefined),
  navigate: vi.fn(),
  setTheme: vi.fn(),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: 7,
      login: "jdoe",
      first_name: "Jane",
      last_name: "Doe",
      email: "jane@example.com",
    },
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
  });

  it("shows the signed-in identity and the core actions", () => {
    open();
    expect(screen.getByTestId("account-menu-name")).toHaveTextContent("Jane Doe");
    expect(screen.getByTestId("current-user")).toHaveTextContent("Jane Doe");
    expect(screen.queryByTestId("account-menu-settings")).not.toBeInTheDocument();
    expect(screen.getByTestId("account-menu-security")).toBeInTheDocument();
    expect(screen.getByTestId("account-menu-lang-de")).toBeInTheDocument();
    expect(screen.getByTestId("account-menu-lang-en")).toBeInTheDocument();
    expect(screen.getByTestId("account-menu-theme-light")).toBeInTheDocument();
    expect(screen.getByTestId("logout-btn")).toBeInTheDocument();
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

  it("changes language and persists the choice", () => {
    const changeLanguage = vi.spyOn(i18n, "changeLanguage");
    open();
    fireEvent.click(screen.getByTestId("account-menu-lang-en"));
    expect(changeLanguage).toHaveBeenCalledWith("en");
    expect(localStorage.getItem("tiqora-lang")).toBe("en");
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

  it("renders a Gravatar image when the user has an email", () => {
    open();
    const img = screen.getByTestId("account-menu-avatar");
    expect(img.tagName).toBe("IMG");
    expect(img).toHaveAttribute(
      "src",
      expect.stringMatching(/^https:\/\/www\.gravatar\.com\/avatar\/[0-9a-f]{32}\?/),
    );
  });
});
