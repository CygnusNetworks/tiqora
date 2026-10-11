/**
 * Plugin slots of the agent app (P12): sidebar entries and panels in the
 * ticket view. Each plugin component sits in its own PluginBoundary, so one
 * broken plugin never hides the others. Dashboard widgets of plugins are
 * placed by the home page's grid (`DASHBOARD_WIDGETS`), not here.
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { NavItem } from "@/components/layout/NavItem";
import {
  BookIcon,
  CalendarIcon,
  ChartIcon,
  ClockIcon,
  InboxIcon,
  MailIcon,
  PuzzleIcon,
  SearchIcon,
  ServerIcon,
  ShieldIcon,
  UserIcon,
} from "@/components/ui/icons";
import { pluginNamespace } from "./i18n";
import { PluginBoundary } from "./PluginBoundary";
import { PluginComponent } from "./PluginComponent";
import { usePluginContributions, usePlugins } from "./PluginProvider";
import { useVisibleNavEntries } from "./useNavGroupHasItems";

const NAV_ICONS: Record<string, () => ReactNode> = {
  book: () => <BookIcon />,
  calendar: () => <CalendarIcon />,
  chart: () => <ChartIcon />,
  clock: () => <ClockIcon />,
  inbox: () => <InboxIcon />,
  mail: () => <MailIcon />,
  puzzle: () => <PuzzleIcon />,
  search: () => <SearchIcon />,
  server: () => <ServerIcon />,
  shield: () => <ShieldIcon />,
  user: () => <UserIcon />,
};

/** The plugin's icon name; unknown names fall back to the puzzle piece. */
function navIcon(name: string): ReactNode {
  return (NAV_ICONS[name] ?? NAV_ICONS.puzzle)();
}

/**
 * Sidebar entries that plugins contributed to this group. `route` is the key
 * of an `agent.route` of the same plugin; the link goes to that route's path.
 * An entry without such a route is skipped (the backend already drops it).
 */
export function PluginNavItems({ group, onNavigate }: { group: string; onNavigate?: () => void }) {
  const { t } = useTranslation();
  const entries = useVisibleNavEntries(group);
  return (
    <>
      {entries.map(({ plugin, contribution, path, exact }) => (
        <NavItem
          key={`${plugin.id}:${contribution.key}`}
          to={`/agent/plugins/${plugin.id}/${path}`}
          exact={exact}
          label={t(`${pluginNamespace(plugin.id)}:${contribution.label_key}`, {
            defaultValue: contribution.key,
          })}
          icon={navIcon(contribution.icon)}
          testId={`nav-plugin-${plugin.id}-${contribution.key}`}
          onNavigate={onNavigate}
        />
      ))}
    </>
  );
}

/** Small note in the sidebar while browser safe mode (`?plugins=off`) is on. */
export function PluginSafeModeNote() {
  const { t } = useTranslation();
  const { safeMode } = usePlugins();
  if (!safeMode) return null;
  return (
    <p role="status" className="mt-3 px-2.5 text-xs text-muted" data-testid="plugin-safe-mode-note">
      {t("plugins.safeModeBrowser")}
    </p>
  );
}

/** Panels of plugins in the ticket view. Renders nothing without any. */
export function PluginTicketPanels({ ticketId }: { ticketId: number }) {
  const panels = usePluginContributions("ticket.sidebar");
  if (panels.length === 0) return null;
  return (
    <>
      {panels.map(({ plugin, contribution }) => (
        <div key={`${plugin.id}:${contribution.key}`} data-testid={`plugin-ticket-panel-${plugin.id}-${contribution.key}`}>
          {/* keyed: a panel that failed for one ticket gets a fresh start on the next */}
          <PluginBoundary key={ticketId} plugin={plugin}>
            <PluginComponent plugin={plugin} name={contribution.component} props={{ ticketId }} />
          </PluginBoundary>
        </div>
      ))}
    </>
  );
}
