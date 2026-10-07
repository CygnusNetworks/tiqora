import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { browserTimeZone } from "@/lib/timeZone";
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

const { setMyTimeZone } = vi.hoisted(() => ({ setMyTimeZone: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { setMyTimeZone },
}));

let queryClient: QueryClient;

function open() {
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <AccountMenu />
      </I18nextProvider>
    </QueryClientProvider>,
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
    expect(screen.getByTestId("account-menu-theme-system")).toBeInTheDocument();
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

  it("changes language via the SelectMenu and persists the choice, keeping the menu open", async () => {
    const changeLanguage = vi.spyOn(i18n, "changeLanguage");
    open();
    fireEvent.click(screen.getByTestId("account-menu-lang-select"));
    fireEvent.click(within(screen.getByTestId("account-menu-lang-panel")).getByText("English"));
    await vi.waitFor(() => expect(changeLanguage).toHaveBeenCalledWith("en"));
    expect(localStorage.getItem("tiqora-lang")).toBe("en");
    // The surrounding account Menu stays open — SelectMenu's portal panel is
    // recognized as "inside" by Menu.tsx's outside-pointerdown handler.
    expect(screen.getByTestId("account-menu")).toBeInTheDocument();
    changeLanguage.mockRestore();
  });

  it("keeps the account menu open while scrolling the language list", () => {
    // Regression: Menu closed on any non-self scroll, including the portaled
    // SelectMenu panel — so users could not reach languages below the fold.
    open();
    fireEvent.click(screen.getByTestId("account-menu-lang-select"));
    const langPanel = screen.getByTestId("account-menu-lang-panel");
    fireEvent.scroll(langPanel);
    expect(screen.getByTestId("account-menu")).toBeInTheDocument();
    expect(langPanel).toBeInTheDocument();
    // Deep option still reachable after scroll.
    expect(within(langPanel).getByText("日本語")).toBeInTheDocument();
  });

  it("saves a picked time zone and puts the returned /me into the auth query", async () => {
    const updated = { ...authUser, time_zone: "Asia/Tokyo", default_time_zone: "Europe/Berlin" };
    setMyTimeZone.mockReset().mockResolvedValue(updated);
    open();
    const trigger = screen.getByTestId("account-menu-tz-select");
    // No preference yet: the browser zone is the current choice.
    expect(trigger).toHaveTextContent(i18n.t("account.timeZoneBrowser", { zone: browserTimeZone() }));
    fireEvent.click(trigger);
    const panel = screen.getByTestId("account-menu-tz-panel");
    fireEvent.click(within(panel).getByText("Asia/Tokyo"));
    await vi.waitFor(() => expect(setMyTimeZone).toHaveBeenCalledWith("Asia/Tokyo"));
    await vi.waitFor(() => expect(queryClient.getQueryData(["auth", "me"])).toEqual(updated));
  });

  it("clears the preference with the browser option and shows save errors", async () => {
    setMyTimeZone.mockReset().mockRejectedValue(new Error("422"));
    open();
    fireEvent.click(screen.getByTestId("account-menu-tz-select"));
    fireEvent.click(
      within(screen.getByTestId("account-menu-tz-panel")).getByText(
        i18n.t("account.timeZoneBrowser", { zone: browserTimeZone() }),
      ),
    );
    await vi.waitFor(() => expect(setMyTimeZone).toHaveBeenCalledWith(null));
    expect(await screen.findByTestId("account-menu-tz-error")).toBeInTheDocument();
  });

  it("toggles theme via setTheme", () => {
    open();
    fireEvent.click(screen.getByTestId("account-menu-theme-light"));
    expect(setTheme).toHaveBeenCalledWith("light");
  });

  it("offers the system theme", () => {
    open();
    fireEvent.click(screen.getByTestId("account-menu-theme-system"));
    expect(setTheme).toHaveBeenCalledWith("system");
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
