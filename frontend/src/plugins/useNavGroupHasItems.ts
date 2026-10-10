import { useMemo } from "react";
import { useAuth } from "@/auth/AuthContext";
import type { FrontendContribution, FrontendPlugin } from "@/lib/pluginsApi";
import { usePluginContributions } from "./PluginProvider";

type NavContribution = Extract<FrontendContribution, { slot: "agent.nav" }>;

export type VisibleNavEntry = {
  plugin: FrontendPlugin;
  contribution: NavContribution;
  /** Path of the `agent.route` the entry points to (by key), without slashes. */
  path: string;
  /** Highlight only on this exact page: another route of the plugin lies below it. */
  exact: boolean;
};

function trimSlashes(path: string): string {
  return path.replace(/^\/+|\/+$/g, "");
}

/**
 * Nav entries of `group` the user would see: admin-only ones only for admins,
 * and only entries whose `route` names an `agent.route` of the same plugin.
 */
export function useVisibleNavEntries(group: string): VisibleNavEntry[] {
  const { user } = useAuth();
  const items = usePluginContributions("agent.nav");
  const isAdmin = Boolean(user?.is_admin);
  return useMemo(() => {
    const out: VisibleNavEntry[] = [];
    for (const { plugin, contribution } of items) {
      if (contribution.group !== group || (contribution.admin_only && !isAdmin)) continue;
      const routes = plugin.contributions.filter(
        (c): c is Extract<FrontendContribution, { slot: "agent.route" }> => c.slot === "agent.route",
      );
      const target = routes.find((c) => c.key === contribution.route);
      if (!target) continue;
      const path = trimSlashes(target.path);
      const exact = routes.some((c) => trimSlashes(c.path).startsWith(`${path}/`));
      out.push({ plugin, contribution, path, exact });
    }
    return out;
  }, [items, group, isAdmin]);
}

/** True when any plugin contributes a nav entry the user would see in `group`. */
export function useNavGroupHasItems(group: string): boolean {
  return useVisibleNavEntries(group).length > 0;
}
