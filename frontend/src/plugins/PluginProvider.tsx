/**
 * Frontend plugin list for the agent and admin shells (P12). Fetches
 * `GET /api/v1/plugins/frontend` once (an error means "no plugins", no
 * toast), registers dashboard widgets and loads the plugins' translations.
 * In browser safe mode (`?plugins=off`) it fetches and loads nothing.
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { pluginsApi, type FrontendContribution, type FrontendPlugin } from "@/lib/pluginsApi";
import { registerPluginWidgets } from "./dashboardWidgets";
import { ensurePluginI18n } from "./i18n";
import { applySafeModeFromUrl, isPluginSafeMode } from "./safeMode";

type PluginsState = { plugins: FrontendPlugin[]; ready: boolean; safeMode: boolean };
type PluginsContextValue = PluginsState & { textsRevision: number };

const NO_PLUGINS: FrontendPlugin[] = [];
const PluginsContext = createContext<PluginsContextValue>({
  plugins: NO_PLUGINS,
  ready: false,
  safeMode: false,
  textsRevision: 0,
});

export function PluginProvider({ children }: { children: ReactNode }) {
  const [safeMode] = useState(() => {
    applySafeModeFromUrl(window.location.search);
    return isPluginSafeMode();
  });
  const { i18n } = useTranslation();
  const query = useQuery({
    queryKey: ["plugins", "frontend"],
    queryFn: ({ signal }) => pluginsApi.frontend(signal),
    enabled: !safeMode,
    staleTime: Infinity,
    retry: false,
  });
  const plugins = safeMode ? NO_PLUGINS : (query.data ?? NO_PLUGINS);
  const ready = safeMode || query.isSuccess || query.isError;
  // Bumped when plugin translations arrived: react-i18next does not re-render
  // on addResourceBundle, so consumers (nav labels, …) re-render through this.
  const [textsRevision, setTextsRevision] = useState(0);

  useEffect(() => {
    registerPluginWidgets(plugins);
  }, [plugins]);

  useEffect(() => {
    let active = true;
    const load = (lang: string) => {
      const withTexts = plugins.filter((p) => p.locales.length > 0);
      if (withTexts.length === 0) return;
      void Promise.all(withTexts.map((p) => ensurePluginI18n(p, lang))).then(() => {
        if (active) setTextsRevision((n) => n + 1);
      });
    };
    load(i18n.language);
    i18n.on("languageChanged", load);
    return () => {
      active = false;
      i18n.off("languageChanged", load);
    };
  }, [plugins, i18n]);

  const value = useMemo(
    () => ({ plugins, ready, safeMode, textsRevision }),
    [plugins, ready, safeMode, textsRevision],
  );
  return <PluginsContext.Provider value={value}>{children}</PluginsContext.Provider>;
}

export function usePlugins(): PluginsState {
  const { plugins, ready, safeMode } = useContext(PluginsContext);
  return { plugins, ready, safeMode };
}

type SlotItem<S extends FrontendContribution["slot"]> = {
  plugin: FrontendPlugin;
  contribution: Extract<FrontendContribution, { slot: S }>;
};

function orderOf(c: FrontendContribution): number {
  return "order" in c ? c.order : 100;
}

/** Every contribution to `slot`, sorted by `order`, then plugin id, then key. */
export function usePluginContributions<S extends FrontendContribution["slot"]>(slot: S): SlotItem<S>[] {
  const { plugins } = useContext(PluginsContext);
  return useMemo(() => {
    const items: SlotItem<S>[] = [];
    for (const plugin of plugins) {
      for (const c of plugin.contributions) {
        if (c.slot === slot) items.push({ plugin, contribution: c as Extract<FrontendContribution, { slot: S }> });
      }
    }
    return items.sort(
      (a, b) =>
        orderOf(a.contribution) - orderOf(b.contribution) ||
        a.plugin.id.localeCompare(b.plugin.id) ||
        a.contribution.key.localeCompare(b.contribution.key),
    );
  }, [plugins, slot]);
}
