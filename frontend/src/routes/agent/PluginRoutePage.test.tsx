import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { helloPlugin, routePage } from "@/test/pluginFixtures";
import { DASHBOARD_WIDGETS } from "@/plugins/dashboardWidgets";
import { setPluginImporterForTests } from "@/plugins/loader";
import { PluginProvider } from "@/plugins/PluginProvider";
import { PluginRoutePage, resolvePluginRoute } from "./PluginRoutePage";

const { frontend } = vi.hoisted(() => ({ frontend: vi.fn() }));
vi.mock("@/lib/pluginsApi", () => ({ pluginsApi: { frontend } }));

const auth = vi.hoisted(() => ({ isAdmin: false }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, login: "agent", is_admin: auth.isAdmin } }),
}));

const route = (path: string) => routePage(path.replace(/\W/g, "_"), path);

function HelloPage({ path, subPath }: { path: string; subPath: string }) {
  if (path === "boom") throw new Error("kaputt");
  return <p>Seite {path} / {subPath}</p>;
}

async function renderAt(url: string) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "/agent/plugins/$pluginId/$", component: PluginRoutePage });
  const router = createRouter({
    routeTree: root.addChildren([page]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  await router.load();
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nextProvider i18n={i18n}>
        <PluginProvider>
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          <RouterProvider router={router as any} />
        </PluginProvider>
      </I18nextProvider>
    </QueryClientProvider>,
  ) && router;
}

beforeEach(async () => {
  window.scrollTo = vi.fn();
  auth.isAdmin = false;
  sessionStorage.clear();
  DASHBOARD_WIDGETS.clearForTests();
  setPluginImporterForTests(() => Promise.resolve({ default: { components: { HelloPage } } }));
  frontend.mockReset().mockResolvedValue([
    helloPlugin({
      locales: [],
      contributions: [routePage("overview", "overview"), { ...routePage("admin", "admin"), admin_only: true }],
    }),
  ]);
  await i18n.changeLanguage("de");
  i18n.addResourceBundle("de", "plugin.org.example.hello", { page: { overview: "Übersicht" } }, true, true);
});

describe("resolvePluginRoute", () => {
  it("resolves the longest matching path and passes the rest as subPath", () => {
    const cs = [route("overview"), route("overview/details"), route("admin")];
    expect(resolvePluginRoute(cs, "overview/details/7")).toMatchObject({
      contribution: { path: "overview/details" },
      subPath: "7",
    });
    expect(resolvePluginRoute(cs, "overview")).toMatchObject({ contribution: { path: "overview" }, subPath: "" });
    expect(resolvePluginRoute(cs, "overview/x")).toMatchObject({ contribution: { path: "overview" }, subPath: "x" });
    expect(resolvePluginRoute(cs, "overviewx")).toBeNull();
    expect(resolvePluginRoute(cs, "")).toBeNull();
  });

  it("refuses sub paths with dot segments, also encoded", () => {
    const cs = [route("overview"), route("admin")];
    for (const splat of ["overview/../admin", "overview/./x", "overview/a/..", "overview/%2e%2e/admin", "overview/%2E/x", "overview/%zz"]) {
      expect(resolvePluginRoute(cs, splat)).toBeNull();
    }
    expect(resolvePluginRoute(cs, "overview/a.b/..c")).toMatchObject({ subPath: "a.b/..c" });
  });
});

describe("PluginRoutePage", () => {
  it("a crashed page does not stick when the agent opens another page of the same plugin", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    frontend.mockResolvedValue([
      helloPlugin({ locales: [], contributions: [routePage("boom", "boom"), routePage("fine", "fine")] }),
    ]);
    const router = await renderAt("/agent/plugins/org.example.hello/boom");
    expect(await screen.findByText("Erweiterung „Hallo“ konnte nicht geladen werden.")).toBeInTheDocument();
    await act(() => router.history.push("/agent/plugins/org.example.hello/fine"));
    expect(await screen.findByText("Seite fine /")).toBeInTheDocument();
  });

  it("renders the plugin component with the title and sub path", async () => {
    await renderAt("/agent/plugins/org.example.hello/overview/7");
    expect(await screen.findByRole("heading", { name: "Übersicht" })).toBeInTheDocument();
    expect(await screen.findByText("Seite overview / 7")).toBeInTheDocument();
  });

  it("unknown plugin page shows the not-found text", async () => {
    await renderAt("/agent/plugins/org.example.hello/nope");
    expect(await screen.findByText("Diese Seite der Erweiterung gibt es nicht.")).toBeInTheDocument();
  });

  it("unknown plugin shows the not-found text", async () => {
    await renderAt("/agent/plugins/org.example.other/overview");
    expect(await screen.findByText("Diese Seite der Erweiterung gibt es nicht.")).toBeInTheDocument();
  });

  it("an admin-only page is not found for agents but opens for admins", async () => {
    await renderAt("/agent/plugins/org.example.hello/admin");
    expect(await screen.findByText("Diese Seite der Erweiterung gibt es nicht.")).toBeInTheDocument();
    expect(screen.queryByText(/Seite admin/)).toBeNull();
  });

  it("opens an admin-only page for admins", async () => {
    auth.isAdmin = true;
    await renderAt("/agent/plugins/org.example.hello/admin");
    expect(await screen.findByText("Seite admin /")).toBeInTheDocument();
  });
});
