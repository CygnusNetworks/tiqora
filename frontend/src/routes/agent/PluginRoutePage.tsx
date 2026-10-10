/**
 * Page of a plugin inside the agent shell: `/agent/plugins/<pluginId>/<path…>`.
 * The longest matching `agent.route` path of the plugin wins; the rest of the
 * URL goes to the component as `subPath`.
 */
import { useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { Spinner } from "@/components/ui/Spinner";
import type { FrontendContribution } from "@/lib/pluginsApi";
import { pluginNamespace } from "@/plugins/i18n";
import { PluginBoundary } from "@/plugins/PluginBoundary";
import { PluginComponent } from "@/plugins/PluginComponent";
import { usePlugins, usePluginContributions } from "@/plugins/PluginProvider";

type RouteContribution = Extract<FrontendContribution, { slot: "agent.route" }>;

// eslint-disable-next-line react-refresh/only-export-components -- exported for tests
export function resolvePluginRoute(
  contributions: RouteContribution[],
  splat: string,
): { contribution: RouteContribution; subPath: string } | null {
  const rest = splat.replace(/^\/+|\/+$/g, "");
  let best: RouteContribution | null = null;
  for (const c of contributions) {
    const path = c.path.replace(/^\/+|\/+$/g, "");
    if (rest !== path && !rest.startsWith(`${path}/`)) continue;
    if (!best || path.length > best.path.replace(/^\/+|\/+$/g, "").length) best = c;
  }
  if (!best) return null;
  const path = best.path.replace(/^\/+|\/+$/g, "");
  return { contribution: best, subPath: rest.slice(path.length).replace(/^\/+/, "") };
}

export function PluginRoutePage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { pluginId = "", _splat = "" } = useParams({ strict: false }) as { pluginId?: string; _splat?: string };
  const { plugins, ready } = usePlugins();
  const routes = usePluginContributions("agent.route");
  const plugin = plugins.find((p) => p.id === pluginId);
  const own = routes.filter((r) => r.plugin.id === pluginId).map((r) => r.contribution);
  const match = plugin ? resolvePluginRoute(own, _splat) : null;

  if (!ready) {
    return (
      <div className="flex justify-center p-8">
        <Spinner />
      </div>
    );
  }
  if (!plugin || !match || (match.contribution.admin_only && !user?.is_admin)) {
    return (
      <p className="p-6 text-sm text-muted" data-testid="plugin-page-not-found">
        {t("plugins.pageNotFound")}
      </p>
    );
  }
  const { contribution, subPath } = match;
  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6" data-testid={`plugin-page-${plugin.id}`}>
      <h1 className="font-display text-2xl font-semibold text-ink">
        {t(`${pluginNamespace(plugin.id)}:${contribution.title_key}`, { defaultValue: contribution.key })}
      </h1>
      <PluginBoundary plugin={plugin}>
        <PluginComponent plugin={plugin} name={contribution.component} props={{ path: contribution.path, subPath }} />
      </PluginBoundary>
    </div>
  );
}
