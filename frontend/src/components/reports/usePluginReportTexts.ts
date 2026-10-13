import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { pluginsApi } from "@/lib/pluginsApi";
import { ensurePluginI18n } from "@/plugins/i18n";

/** The plugin behind a text key `plugin.<id>:<key>` (null for core keys). */
export function pluginIdOfKey(key: string | null | undefined): string | null {
  if (!key || !key.startsWith("plugin.")) return null;
  const colon = key.indexOf(":");
  return colon > "plugin.".length ? key.slice("plugin.".length, colon) : null;
}

type KeyedReport = { title_key?: string | null; description_key?: string | null } | null | undefined;

/**
 * Plugin reports carry texts in the plugin's own i18n namespace, which is only
 * loaded on demand. Loads the files of every plugin the given reports belong to
 * (the plugin objects come from the cached `GET /plugins/frontend`) and re-renders
 * the caller once they are there; until then `t()` renders the key's fallback.
 * `loading` is true while a needed file is still on its way. Core reports never
 * trigger a request.
 */
export function usePluginReportTexts(reports: readonly KeyedReport[]): { loading: boolean } {
  const { i18n } = useTranslation();
  const lang = i18n.language;
  const idsKey = useMemo(() => {
    const ids = new Set<string>();
    for (const r of reports) {
      for (const k of [r?.title_key, r?.description_key]) {
        const id = pluginIdOfKey(k);
        if (id) ids.add(id);
      }
    }
    return [...ids].sort().join("\n");
  }, [reports]);
  const ids = useMemo(() => (idsKey ? idsKey.split("\n") : []), [idsKey]);

  const pluginsQ = useQuery({
    queryKey: ["plugins", "frontend"],
    queryFn: ({ signal }) => pluginsApi.frontend(signal),
    staleTime: Infinity,
    retry: false,
    enabled: ids.length > 0,
  });
  const plugins = pluginsQ.data;
  // `id|lang` of the files that have arrived; the revision re-renders the caller.
  const [loaded, setLoaded] = useState<string>("");

  const wanted = `${idsKey}|${lang}`;
  useEffect(() => {
    if (ids.length === 0 || !plugins) return;
    let active = true;
    const found = ids
      .map((id) => plugins.find((p) => p.id === id))
      .filter((p): p is NonNullable<typeof p> => p !== undefined);
    void Promise.all(found.map((p) => ensurePluginI18n(p, lang))).then(() => {
      if (active) setLoaded(wanted);
    });
    return () => {
      active = false;
    };
  }, [ids, plugins, lang, wanted]);

  const loading = ids.length > 0 && (pluginsQ.isPending || loaded !== wanted);
  return { loading: loading && !pluginsQ.isError };
}
