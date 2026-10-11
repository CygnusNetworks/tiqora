/**
 * `/?plugins=off` (the documented recovery URL) must survive the HomeRedirect:
 * main.tsx stores the flag before the router drops the query string.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import {
  RouterProvider,
  createBrowserHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import { PluginProvider, usePlugins } from "@/plugins/PluginProvider";
import { isPluginSafeMode } from "@/plugins/safeMode";
import { HomeRedirect } from "./HomeRedirect";

const { frontend, render: rootRender } = vi.hoisted(() => ({ frontend: vi.fn(), render: vi.fn() }));
vi.mock("@/lib/pluginsApi", () => ({ pluginsApi: { frontend } }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ isAuthenticated: true, isLoading: false, user: { id: 1, is_admin: false } }),
}));
vi.mock("@/lib/usePortalEnabled", () => ({ usePortalEnabled: () => ({ portalEnabled: false, isLoading: false }) }));
// main.tsx boots for real, but renders nothing here
vi.mock("react-dom/client", () => ({ createRoot: () => ({ render: rootRender }) }));
vi.mock("../App", () => ({ default: () => null }));

function AgentProbe() {
  const { safeMode } = usePlugins();
  return <p>{safeMode ? "Erweiterungen aus" : "Erweiterungen an"}</p>;
}

beforeEach(() => {
  window.scrollTo = vi.fn();
  sessionStorage.clear();
  frontend.mockReset().mockResolvedValue([]);
  document.body.innerHTML = '<div id="root"></div>';
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("browser safe mode via /?plugins=off", () => {
  it("is stored by main.tsx and still on after the redirect to /agent", async () => {
    window.history.replaceState(null, "", "/?plugins=off");
    await import("../main");
    expect(rootRender).toHaveBeenCalled(); // the boot ran
    expect(isPluginSafeMode()).toBe(true);

    const root = createRootRoute();
    const home = createRoute({ getParentRoute: () => root, path: "/", component: HomeRedirect });
    const agent = createRoute({
      getParentRoute: () => root,
      path: "/agent",
      component: () => (
        <PluginProvider>
          <AgentProbe />
        </PluginProvider>
      ),
    });
    const router = createRouter({ routeTree: root.addChildren([home, agent]), history: createBrowserHistory() });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nextProvider i18n={i18n}>
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          <RouterProvider router={router as any} />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Erweiterungen aus")).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/agent"));
    expect(window.location.search).toBe(""); // the redirect dropped the query string ...
    expect(frontend).not.toHaveBeenCalled(); // ... and still no plugin list, no plugin code
  });
});
