import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SettingsPage } from "./SettingsPage";

let theme: "light" | "dark" = "dark";
const setTheme = vi.fn((mode: "light" | "dark") => {
  theme = mode;
});

vi.mock("@/themes/theme", () => ({
  useTheme: () => ({ theme, setTheme }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

function renderPage() {
  return render(
    <I18nextProvider i18n={i18n}>
      <SettingsPage />
    </I18nextProvider>,
  );
}

describe("SettingsPage", () => {
  beforeEach(() => {
    setTheme.mockClear();
    theme = "dark";
    localStorage.clear();
    void i18n.changeLanguage("en");
  });

  it("renders the settings sections and security link", () => {
    renderPage();
    expect(screen.getByTestId("settings-page")).toBeInTheDocument();
    expect(screen.getByTestId("settings-security-link")).toHaveAttribute(
      "href",
      "/agent/security",
    );
  });

  it("switches language and persists the choice to localStorage", () => {
    renderPage();
    fireEvent.click(screen.getByTestId("settings-lang-de"));
    expect(localStorage.getItem("tiqora-lang")).toBe("de");
  });

  it("calls setTheme when a theme button is clicked", () => {
    renderPage();
    fireEvent.click(screen.getByTestId("settings-theme-light"));
    expect(setTheme).toHaveBeenCalledWith("light");
  });
});
