import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { pluginsApi } from "@/lib/pluginsApi";
import { ensurePluginI18n } from "@/plugins/i18n";
import { isPluginSafeMode } from "@/plugins/safeMode";

/** The plugin behind a text key `plugin.<id>:<key>` (null for core keys). */
export function pluginIdOfKey(key: string | null | undefined): string | null {
  if (!key || !key.startsWith("plugin.")) return null;
  const colon = key.indexOf(":");
  return colon > "plugin.".length ? key.slice("plugin.".length, colon) : null;
}

type KeyedReport = { title_key?: string | null; description_key?: string | null } | null | undefined;
type TextKey = string | null | undefined;

const NO_KEYS: readonly TextKey[] = [];

/**
 * Every `label_key` in a report source, result or definition (source, dimensions, measures,
 * filter fields, parameters, result columns), wherever it sits in the object.
 */
export function labelKeysOf(value: unknown, out: string[] = []): string[] {
  if (Array.isArray(value)) {
    for (const v of value) labelKeysOf(v, out);
  } else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) {
      if (k === "label_key" && typeof v === "string") out.push(v);
      else if (v && typeof v === "object") labelKeysOf(v, out);
    }
  }
  return out;
}

/**
 * Plugin reports carry texts in the plugin's own i18n namespace, which is only
 * loaded on demand. Loads the files of every plugin the given reports belong to —
 * their titles and descriptions, plus any `labelKeys` (source, dimension, measure and
 * column labels, see {@link labelKeysOf}) — (the plugin objects come from the cached
 * `GET /plugins/frontend`) and re-renders the caller once they are there; until then
 * `t()` renders the key's fallback. `loading` is true while a needed file is still on
 * its way. Core keys never trigger a request, and neither does browser safe mode
 * (`?plugins=off`), which loads nothing from plugins.
 */
export function usePluginReportTexts(
  reports: readonly KeyedReport[],
  labelKeys: readonly TextKey[] = NO_KEYS,
): { loading: boolean } {
  const { i18n } = useTranslation();
  const lang = i18n.language;
  const safeMode = isPluginSafeMode();
  const idsKey = useMemo(() => {
    const ids = new Set<string>();
    const keys: TextKey[] = [...labelKeys];
    for (const r of reports) keys.push(r?.title_key, r?.description_key);
    for (const k of keys) {
      const id = pluginIdOfKey(k);
      if (id) ids.add(id);
    }
    return [...ids].sort().join("\n");
  }, [reports, labelKeys]);
  const ids = useMemo(() => (idsKey && !safeMode ? idsKey.split("\n") : []), [idsKey, safeMode]);

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
