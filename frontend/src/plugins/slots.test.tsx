import { beforeEach, describe, expect, it, vi } from "vitest";
import { useState, type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import { dashboardWidget, helloPlugin, navEntry, ticketPanel } from "@/test/pluginFixtures";
import type { FrontendPlugin } from "@/lib/pluginsApi";
import { DASHBOARD_WIDGETS } from "./dashboardWidgets";
import { setPluginImporterForTests } from "./loader";
import { PluginProvider } from "./PluginProvider";
import { PluginNavItems, PluginTicketPanels, PluginWidgetsSection } from "./slots";

const { frontend } = vi.hoisted(() => ({ frontend: vi.fn() }));
vi.mock("@/lib/pluginsApi", () => ({ pluginsApi: { frontend } }));

const auth = vi.hoisted(() => ({ isAdmin: false }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, login: "agent", is_admin: auth.isAdmin } }),
}));

const HELLO: FrontendPlugin = helloPlugin({
  // The texts are pre-loaded below; no request for them in these tests.
  locales: [],
  contributions: [
    navEntry("overview", "overview"),
    { ...navEntry("admin", "admin"), admin_only: true },
    ticketPanel("info"),
    dashboardWidget("weather", {
      settings_schema: { type: "object", properties: { city: { type: "string", default: "Bonn" } } },
    }),
  ],
});

const HelloPanel = ({ ticketId }: { ticketId: number }) => <p>Ticket {ticketId}</p>;
const HelloWidget = ({ settings }: { settings: { city: string } }) => <p>Stadt {settings.city}</p>;

function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  const [router] = useState(() => {
    const root = createRootRoute({ component: () => <>{children}</> });
    const splat = createRoute({
      getParentRoute: () => root,
      path: "/agent/plugins/$pluginId/$",
      component: () => null,
    });
    return createRouter({
      routeTree: root.addChildren([splat]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });
  });
  return (
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <PluginProvider>
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          <RouterProvider router={router as any} />
        </PluginProvider>
      </I18nextProvider>
    </QueryClientProvider>
  );
}

beforeEach(async () => {
  window.scrollTo = vi.fn();
  frontend.mockReset().mockResolvedValue([HELLO]);
  auth.isAdmin = false;
  sessionStorage.clear();
  DASHBOARD_WIDGETS.clearForTests();
  setPluginImporterForTests(null);
  await i18n.changeLanguage("de");
  i18n.addResourceBundle(
    "de",
    "plugin.org.example.hello",
    { nav: { overview: "Hallo", admin: "Hallo (Admin)" } },
    true,
    true,
  );
});

describe("PluginNavItems", () => {
  it("renders nav entries of a group as links to the plugin page, hiding admin-only ones for agents", async () => {
    render(<Providers><PluginNavItems group="plugins" /></Providers>);
    const link = await screen.findByRole("link", { name: "Hallo" });
    expect(link).toHaveAttribute("href", "/agent/plugins/org.example.hello/overview");
    expect(link).toHaveAttribute("data-testid", "nav-plugin-org.example.hello-overview");
    expect(screen.queryByRole("link", { name: "Hallo (Admin)" })).toBeNull();
  });

  it("shows admin-only entries to admins", async () => {
    auth.isAdmin = true;
    render(<Providers><PluginNavItems group="plugins" /></Providers>);
    expect(await screen.findByRole("link", { name: "Hallo (Admin)" })).toHaveAttribute(
      "href",
      "/agent/plugins/org.example.hello/admin",
    );
  });

  it("renders only the entries of the requested group", async () => {
    render(<Providers><PluginNavItems group="workspace" /></Providers>);
    await waitFor(() => expect(frontend).toHaveBeenCalled());
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("nav label falls back to the contribution key without translations", async () => {
    frontend.mockResolvedValue([
      helloPlugin({ id: "org.example.untranslated", locales: [], contributions: [navEntry("overview", "overview")] }),
    ]);
    render(<Providers><PluginNavItems group="plugins" /></Providers>);
    expect(await screen.findByRole("link", { name: "overview" })).toHaveAttribute(
      "href",
      "/agent/plugins/org.example.untranslated/overview",
    );
  });
});

describe("PluginTicketPanels", () => {
  it("renders ticket panels in order with the ticket id", async () => {
    setPluginImporterForTests(() => Promise.resolve({ default: { components: { HelloPanel } } }));
    render(<Providers><PluginTicketPanels ticketId={42} /></Providers>);
    expect(await screen.findByText("Ticket 42")).toBeInTheDocument();
    expect(screen.getByTestId("plugin-ticket-panel-org.example.hello-info")).toBeInTheDocument();
  });

  it("renders nothing without panels", async () => {
    frontend.mockResolvedValue([helloPlugin({ locales: [] })]);
    const { container } = render(<Providers><PluginTicketPanels ticketId={1} /></Providers>);
    await waitFor(() => expect(frontend).toHaveBeenCalled());
    expect(container.querySelector("[data-testid^='plugin-ticket-panel-']")).toBeNull();
  });

  it("a broken panel does not hide the others", async () => {
    const BROKEN = helloPlugin({
      id: "org.example.broken",
      name: "Kaputt",
      locales: [],
      contributions: [ticketPanel("info")],
    });
    frontend.mockResolvedValue([HELLO, BROKEN]);
    vi.spyOn(console, "error").mockImplementation(() => {});
    setPluginImporterForTests((url) =>
      url.includes("org.example.broken")
        ? Promise.reject(new Error("404"))
        : Promise.resolve({ default: { components: { HelloPanel } } }),
    );
    render(<Providers><PluginTicketPanels ticketId={7} /></Providers>);
    expect(await screen.findByText("Ticket 7")).toBeInTheDocument();
    expect(await screen.findByText("Erweiterung „Kaputt“ konnte nicht geladen werden.")).toBeInTheDocument();
  });
});

describe("PluginWidgetsSection", () => {
  it("is absent without plugin widgets", async () => {
    frontend.mockResolvedValue([]);
    render(<Providers><PluginWidgetsSection /></Providers>);
    await waitFor(() => expect(frontend).toHaveBeenCalled());
    expect(screen.queryByTestId("plugin-widgets")).toBeNull();
  });

  it("lists plugin widgets with their default settings and column span", async () => {
    setPluginImporterForTests(() => Promise.resolve({ default: { components: { HelloWidget } } }));
    render(<Providers><PluginWidgetsSection /></Providers>);
    expect(await screen.findByRole("heading", { name: "Erweiterungen" })).toBeInTheDocument();
    expect(await screen.findByText("Stadt Bonn")).toBeInTheDocument();
    expect(screen.getByTestId("plugin-widget-org.example.hello.weather")).toHaveClass("md:col-span-4");
  });
});
