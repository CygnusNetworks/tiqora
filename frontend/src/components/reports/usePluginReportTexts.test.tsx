import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { PLUGIN_SAFE_MODE_KEY } from "@/plugins/safeMode";
import { labelKeysOf, usePluginReportTexts } from "./usePluginReportTexts";

const { ensurePluginI18n, pluginsFrontend } = vi.hoisted(() => ({
  ensurePluginI18n: vi.fn(async () => undefined),
  pluginsFrontend: vi.fn(),
}));
vi.mock("@/plugins/i18n", () => ({ ensurePluginI18n }));
vi.mock("@/lib/pluginsApi", () => ({ pluginsApi: { frontend: pluginsFrontend } }));

const plugin = { id: "org.example.points", version: "1.0.0", locales: ["de", "en"] };
const SOURCE = {
  key: "plugin.points.articles_per_channel",
  label_key: "plugin.org.example.points:reports.sources.articles_per_channel.label",
  dimensions: [{ key: "channel", label_key: "plugin.org.example.points:reports.dimensions.channel" }],
  measures: [{ key: "count", label_key: "reports.measures.count" }],
};

type Keyed = { title_key?: string | null };
const NONE: Keyed[] = [];

function Probe({ labelKeys, reports = NONE }: { labelKeys: readonly (string | null | undefined)[]; reports?: Keyed[] }) {
  const { loading } = usePluginReportTexts(reports, labelKeys);
  return <span data-testid="probe">{loading ? "loading" : "ready"}</span>;
}

function renderProbe(labelKeys: readonly (string | null | undefined)[], reports?: Keyed[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <Probe labelKeys={labelKeys} reports={reports} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  pluginsFrontend.mockReset().mockResolvedValue([plugin]);
  ensurePluginI18n.mockClear();
});

afterEach(() => {
  sessionStorage.removeItem(PLUGIN_SAFE_MODE_KEY);
});

describe("usePluginReportTexts", () => {
  it("collects every label_key of a source, wherever it sits", () => {
    expect(labelKeysOf(SOURCE)).toEqual([
      "plugin.org.example.points:reports.sources.articles_per_channel.label",
      "plugin.org.example.points:reports.dimensions.channel",
      "reports.measures.count",
    ]);
    expect(labelKeysOf(undefined)).toEqual([]);
  });

  it("loads the plugin texts behind source and column labels, not only titles", async () => {
    const view = renderProbe(labelKeysOf(SOURCE));
    await waitFor(() => expect(ensurePluginI18n).toHaveBeenCalledWith(plugin, i18n.language));
    await waitFor(() => expect(view.getByTestId("probe").textContent).toBe("ready"));
  });

  it("asks for nothing for core labels", async () => {
    const view = renderProbe(["reports.measures.count", null]);
    expect(view.getByTestId("probe").textContent).toBe("ready");
    await new Promise((r) => setTimeout(r, 20));
    expect(pluginsFrontend).not.toHaveBeenCalled();
  });

  it("loads nothing in browser safe mode", async () => {
    sessionStorage.setItem(PLUGIN_SAFE_MODE_KEY, "1");
    const view = renderProbe(labelKeysOf(SOURCE), [
      { title_key: "plugin.org.example.points:reports.articles_by_channel.title" },
    ]);
    expect(view.getByTestId("probe").textContent).toBe("ready");
    await new Promise((r) => setTimeout(r, 20));
    expect(pluginsFrontend).not.toHaveBeenCalled();
    expect(ensurePluginI18n).not.toHaveBeenCalled();
  });
});
