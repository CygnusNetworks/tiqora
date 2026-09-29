import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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

const { myExtension, setMyExtension } = vi.hoisted(() => ({
  myExtension: vi.fn(),
  setMyExtension: vi.fn(),
}));

vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return { ...actual, phoneApi: { myExtension, setMyExtension } };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SettingsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("SettingsPage", () => {
  beforeEach(() => {
    setTheme.mockClear();
    theme = "dark";
    localStorage.clear();
    void i18n.changeLanguage("en");
    myExtension.mockReset().mockResolvedValue({ extension: "100" });
    setMyExtension.mockReset().mockImplementation(async (ext: string | null) => ({ extension: ext }));
  });

  it("loads and saves the phone extension", async () => {
    renderPage();
    const input = await screen.findByTestId("settings-phone-extension");
    await waitFor(() => expect(input).toHaveValue("100"));
    fireEvent.change(input, { target: { value: " 100, 101 " } });
    fireEvent.click(screen.getByTestId("settings-phone-save"));
    await waitFor(() => expect(setMyExtension).toHaveBeenCalledWith("100, 101"));
    expect(await screen.findByTestId("settings-phone-saved")).toBeInTheDocument();
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
