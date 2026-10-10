/**
 * Loads a plugin's ES module (P12): `import(asset_base + entry)` raced against
 * a timeout, cached per `id@version`, plus one `<link rel="stylesheet">` per
 * style file. Plugin bundles import React & co. from /plugin-runtime/*.js,
 * so they share this page's module instances.
 */
import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { FrontendPlugin } from "@/lib/pluginsApi";

export const PLUGIN_IMPORT_TIMEOUT_MS = 10_000;

export type PluginModule = {
  // Any props: each slot passes its own (RoutePageProps, TicketPanelProps,
  // WidgetProps, …); a narrower type fails under strictFunctionTypes.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  components: Record<string, ComponentType<any>>;
};

type Importer = (url: string) => Promise<unknown>;

const nativeImporter: Importer = (url) => import(/* @vite-ignore */ url);
let customImporter: Importer | null = null;
let testImporter: Importer | null = null;
const cache = new Map<string, Promise<PluginModule>>();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const components = new Map<string, LazyExoticComponent<ComponentType<any>>>();

/** Tests: replace `import()` (null restores it). Also empties the module cache. */
export function setPluginImporterForTests(importer: Importer | null): void {
  testImporter = importer;
  clearPluginModuleCacheForTests();
}

/** Demo mode: plugins come from the app bundle instead of the server. */
export function setPluginImporter(importer: Importer): void {
  customImporter = importer;
  clearPluginModuleCacheForTests();
}

export function clearPluginModuleCacheForTests(): void {
  cache.clear();
  components.clear();
}

const ASSET_PREFIX = "/api/v1/plugins/";

/** Characters/sequences that could leave the plugin's asset directory or this origin. */
function unsafePart(value: string): boolean {
  return value.includes("//") || value.includes(":") || value.includes("..") || /[\\?#\s]/.test(value);
}

/**
 * Same-origin URL of a file below the plugin's asset base (R-SAMEORIGIN).
 * Throws for an asset base outside /api/v1/plugins/ and for a file name that
 * is absolute or could climb out of it.
 */
export function pluginAssetUrl(plugin: FrontendPlugin, file: string): string {
  const base = plugin.asset_base;
  if (!base.startsWith(ASSET_PREFIX) || !base.endsWith("/") || unsafePart(base)) {
    throw new Error(`Erweiterung „${plugin.name}“: ungültige Adresse ${base}`);
  }
  if (!file || file.startsWith("/") || unsafePart(file)) {
    throw new Error(`Erweiterung „${plugin.name}“: ungültige Adresse ${file}`);
  }
  return base + file;
}

function addStyles(urls: string[]): void {
  const present = new Set(
    [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')].map((l) => l.getAttribute("href")),
  );
  for (const href of urls) {
    if (present.has(href)) continue;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.dataset.tiqoraPlugin = "";
    document.head.appendChild(link);
    present.add(href);
  }
}

function isComponentMap(value: unknown): value is PluginModule["components"] {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function load(plugin: FrontendPlugin): Promise<PluginModule> {
  if (!plugin.entry) {
    return Promise.reject(new Error(`Erweiterung „${plugin.name}“ hat keinen Programmteil für die Oberfläche`));
  }
  let entryUrl: string;
  let styleUrls: string[];
  try {
    entryUrl = pluginAssetUrl(plugin, plugin.entry);
    styleUrls = plugin.styles.map((s) => pluginAssetUrl(plugin, s));
  } catch (err) {
    return Promise.reject(err);
  }
  addStyles(styleUrls);

  const importer = testImporter ?? customImporter ?? nativeImporter;
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Armed synchronously, so the timeout runs even if the import never settles.
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(`Zeitüberschreitung: Erweiterung „${plugin.name}“ hat nicht rechtzeitig geladen`));
    }, PLUGIN_IMPORT_TIMEOUT_MS);
  });
  const imported = Promise.resolve()
    .then(() => importer(entryUrl))
    .then((mod) => {
      const def = (mod as { default?: { components?: unknown } } | null)?.default;
      if (!def || !isComponentMap(def.components)) {
        throw new Error(`Erweiterung „${plugin.name}“: ungültiges Modul (kein „components“ im Standard-Export)`);
      }
      return { components: def.components };
    });
  return Promise.race([imported, timeout]).finally(() => clearTimeout(timer));
}

/** The plugin's module; one import per `id@version`. A failed load is retried on the next call. */
export function loadPluginModule(plugin: FrontendPlugin): Promise<PluginModule> {
  const key = `${plugin.id}@${plugin.version}`;
  const cached = cache.get(key);
  if (cached) return cached;
  const promise = load(plugin);
  cache.set(key, promise);
  promise.catch(() => {
    if (cache.get(key) === promise) cache.delete(key);
  });
  return promise;
}

const componentKey = (plugin: FrontendPlugin, name: string) => `${plugin.id}@${plugin.version}:${name}`;

/**
 * One lazy component per plugin version and component name, kept at module
 * level: a component that suspends while mounting loses its hook state, so a
 * lazy created in useMemo would be re-created (and re-imported) on every
 * Suspense retry and a failed load would never reach the error boundary.
 * `prepare` runs alongside the import (the plugin's translations).
 */
export function pluginComponent(
  plugin: FrontendPlugin,
  name: string,
  prepare?: () => Promise<unknown>,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
): LazyExoticComponent<ComponentType<any>> {
  const key = componentKey(plugin, name);
  let component = components.get(key);
  if (!component) {
    component = lazy(() =>
      Promise.all([loadPluginModule(plugin), prepare?.()]).then(([mod]) => {
        const found = mod.components[name];
        if (!found) throw new Error(`Baustein „${name}“ fehlt`);
        return { default: found };
      }),
    );
    components.set(key, component);
  }
  return component;
}

/** After a slot showed an error: the next mount of this plugin version tries again. */
export function forgetPluginComponents(plugin: FrontendPlugin): void {
  const prefix = `${plugin.id}@${plugin.version}:`;
  for (const key of [...components.keys()]) {
    if (key.startsWith(prefix)) components.delete(key);
  }
}
