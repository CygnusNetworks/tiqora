import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState, type ReactNode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import { dashboardWidget, helloPlugin, ticketPanel } from "@/test/pluginFixtures";
import { DASHBOARD_WIDGETS } from "./dashboardWidgets";
import { PluginBoundary } from "./PluginBoundary";
import { PluginComponent } from "./PluginComponent";
import { PluginProvider, usePluginContributions, usePlugins } from "./PluginProvider";
import { PLUGIN_SAFE_MODE_KEY } from "./safeMode";
import { setPluginImporterForTests } from "./loader";

const { frontend } = vi.hoisted(() => ({ frontend: vi.fn() }));
vi.mock("@/lib/pluginsApi", () => ({ pluginsApi: { frontend } }));

const fetchMock = vi.fn();

function Wrapper({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  return (
    <QueryClientProvider client={client}>
      <PluginProvider>{children}</PluginProvider>
    </QueryClientProvider>
  );
}

function Probe() {
  const { plugins, ready, safeMode } = usePlugins();
  return (
    <p data-testid="probe">
      {plugins.length} {safeMode ? "safe" : ready ? "ready" : "loading"}
    </p>
  );
}

/** Renders every ticket panel the way the ticket view does. */
function TicketPanels() {
  const items = usePluginContributions("ticket.sidebar");
  return (
    <>
      {items.map(({ plugin: p, contribution }) => (
        <PluginBoundary key={`${p.id}-${contribution.key}`} plugin={p}>
          <PluginComponent plugin={p} name={contribution.component} props={{ ticketId: 1 }} />
        </PluginBoundary>
      ))}
    </>
  );
}

beforeEach(async () => {
  frontend.mockReset();
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response("{}", { status: 404 }));
  vi.stubGlobal("fetch", fetchMock);
  sessionStorage.clear();
  DASHBOARD_WIDGETS.clearForTests();
  await i18n.changeLanguage("de");
});

afterEach(() => {
  setPluginImporterForTests(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("PluginProvider", () => {
  it("fetches plugins and sorts slot contributions by order", async () => {
    frontend.mockResolvedValue([
      helloPlugin({ id: "org.example.a", contributions: [ticketPanel("late", 200)] }),
      helloPlugin({ id: "org.example.b", contributions: [ticketPanel("early", 50)] }),
    ]);
    const Slots = () => {
      const items = usePluginContributions("ticket.sidebar");
      return <p data-testid="slots">{items.map((i) => `${i.plugin.id}:${i.contribution.key}`).join(",")}</p>;
    };
    render(
      <Wrapper>
        <Slots />
      </Wrapper>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("slots")).toHaveTextContent("org.example.b:early,org.example.a:late"),
    );
  });

  it("ties on order are broken by plugin id; other slots are left out", async () => {
    frontend.mockResolvedValue([
      helloPlugin({ id: "org.example.z", contributions: [ticketPanel("one", 100)] }),
      helloPlugin({ id: "org.example.m", contributions: [ticketPanel("two", 100), dashboardWidget("w")] }),
    ]);
    const Slots = () => {
      const items = usePluginContributions("ticket.sidebar");
      return <p data-testid="slots">{items.map((i) => i.plugin.id).join(",")}</p>;
    };
    render(
      <Wrapper>
        <Slots />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("slots")).toHaveTextContent("org.example.m,org.example.z"));
  });

  it("safe mode skips fetch and import", async () => {
    sessionStorage.setItem(PLUGIN_SAFE_MODE_KEY, "1");
    frontend.mockResolvedValue([helloPlugin({ contributions: [ticketPanel("info")] })]);
    const importer = vi.fn().mockResolvedValue({ default: { components: { HelloPanel: () => <p>Panel</p> } } });
    setPluginImporterForTests(importer);
    render(
      <Wrapper>
        <Probe />
        <TicketPanels />
      </Wrapper>,
    );
    expect(await screen.findByTestId("probe")).toHaveTextContent("0 safe");
    // Give a (wrong) fetch/import every chance to happen.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(frontend).not.toHaveBeenCalled();
    expect(importer).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText("Panel")).toBeNull();
  });

  it("?plugins=off in the address switches safe mode on", async () => {
    window.history.replaceState(null, "", "/agent?plugins=off");
    try {
      render(
        <Wrapper>
          <Probe />
        </Wrapper>,
      );
      expect(await screen.findByTestId("probe")).toHaveTextContent("0 safe");
      expect(frontend).not.toHaveBeenCalled();
      expect(sessionStorage.getItem(PLUGIN_SAFE_MODE_KEY)).toBe("1");
    } finally {
      window.history.replaceState(null, "", "/");
    }
  });

  it("without safe mode, the same panel is imported and rendered", async () => {
    frontend.mockResolvedValue([helloPlugin({ contributions: [ticketPanel("info")] })]);
    const importer = vi.fn().mockResolvedValue({ default: { components: { HelloPanel: () => <p>Panel</p> } } });
    setPluginImporterForTests(importer);
    render(
      <Wrapper>
        <Probe />
        <TicketPanels />
      </Wrapper>,
    );
    expect(await screen.findByText("Panel")).toBeInTheDocument();
    expect(screen.getByTestId("probe")).toHaveTextContent("1 ready");
    expect(importer).toHaveBeenCalledTimes(1);
  });

  it("an API error means no plugins, no crash", async () => {
    frontend.mockRejectedValue(new Error("500"));
    render(
      <Wrapper>
        <Probe />
      </Wrapper>,
    );
    // The query settles after the first render; waitFor instead of the brief's findBy + immediate assert.
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("0 ready"));
    expect(frontend).toHaveBeenCalledTimes(1);
  });

  it("registers dashboard widgets of the loaded plugins", async () => {
    frontend.mockResolvedValue([helloPlugin({ contributions: [dashboardWidget("weather")] })]);
    render(
      <Wrapper>
        <Probe />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("1 ready"));
    await waitFor(() => expect(DASHBOARD_WIDGETS.get("org.example.hello.weather")?.owner).toBe("org.example.hello"));
  });

  it("loads plugin translations and re-renders consumers once they arrive", async () => {
    const p = helloPlugin({ id: "org.example.texts", locales: ["de"], contributions: [ticketPanel("info")] });
    frontend.mockResolvedValue([p]);
    let resolveFetch: (r: Response) => void = () => {};
    fetchMock.mockReturnValueOnce(new Promise<Response>((r) => (resolveFetch = r)));
    const Label = () => {
      usePluginContributions("ticket.sidebar");
      const { t } = useTranslation();
      return <p data-testid="label">{t("plugin.org.example.texts:panel.info", { defaultValue: "info" })}</p>;
    };
    render(
      <Wrapper>
        <Label />
      </Wrapper>,
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/plugins/org.example.texts/assets/1.0.0/i18n/de.json",
        expect.anything(),
      ),
    );
    expect(screen.getByTestId("label")).toHaveTextContent("info");
    await act(async () => {
      resolveFetch(new Response(JSON.stringify({ panel: { info: "Hinweise" } }), { status: 200 }));
    });
    await waitFor(() => expect(screen.getByTestId("label")).toHaveTextContent("Hinweise"));
  });
});
