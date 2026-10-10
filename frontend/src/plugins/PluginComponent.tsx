/**
 * Renders one named component of a plugin module, loaded lazily. Must sit
 * inside a PluginBoundary (Suspense, error containment, instance context).
 */
import { useTranslation } from "react-i18next";
import type { FrontendPlugin } from "@/lib/pluginsApi";
import { ensurePluginI18n } from "./i18n";
import { pluginComponent } from "./loader";

export function PluginComponent({ plugin, name, props }: { plugin: FrontendPlugin; name: string; props: object }) {
  const { i18n } = useTranslation();
  // Module-level lazy (see pluginComponent); the plugin's texts arrive with
  // its code, so its first render is translated.
  const Lazy = pluginComponent(plugin, name, () => ensurePluginI18n(plugin, i18n.language));
  return <Lazy {...props} />;
}
