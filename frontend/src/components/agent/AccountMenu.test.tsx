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
    user: { id: 7, login: "jdoe", first_name: "Jane", last_name: "Doe" },
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
    expect(screen.getByTestId("account-menu-settings")).toBeInTheDocument();
    expect(screen.getByTestId("account-menu-lang-de")).toBeInTheDocument();
    expect(screen.getByTestId("account-menu-lang-en")).toBeInTheDocument();
    expect(screen.getByTestId("account-menu-theme-light")).toBeInTheDocument();
    expect(screen.getByTestId("logout-btn")).toBeInTheDocument();
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

  it("navigates to settings", () => {
    open();
    fireEvent.click(screen.getByTestId("account-menu-settings"));
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/settings" });
  });
});
