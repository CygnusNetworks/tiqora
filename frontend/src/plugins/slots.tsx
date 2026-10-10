/**
 * Plugin slots of the agent app (P12): sidebar entries, panels in the ticket
 * view and widgets on the home page. Each plugin component sits in its own
 * PluginBoundary, so one broken plugin never hides the others.
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
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
import { useDashboardWidgetDefinitions, type WidgetDefinition } from "./dashboardWidgets";
import { pluginNamespace } from "./i18n";
import { PluginBoundary } from "./PluginBoundary";
import { PluginComponent } from "./PluginComponent";
import { usePluginContributions } from "./PluginProvider";

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

/** Sidebar entries that plugins contributed to this group. */
export function PluginNavItems({ group, onNavigate }: { group: string; onNavigate?: () => void }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const items = usePluginContributions("agent.nav");
  return (
    <>
      {items
        .filter(({ contribution }) => contribution.group === group)
        .filter(({ contribution }) => !contribution.admin_only || user?.is_admin)
        .map(({ plugin, contribution }) => (
          <NavItem
            key={`${plugin.id}:${contribution.key}`}
            to={`/agent/plugins/${plugin.id}/${contribution.route}`}
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

/** Panels of plugins in the ticket view. Renders nothing without any. */
export function PluginTicketPanels({ ticketId }: { ticketId: number }) {
  const panels = usePluginContributions("ticket.sidebar");
  if (panels.length === 0) return null;
  return (
    <>
      {panels.map(({ plugin, contribution }) => (
        <div key={`${plugin.id}:${contribution.key}`} data-testid={`plugin-ticket-panel-${plugin.id}-${contribution.key}`}>
          <PluginBoundary plugin={plugin}>
            <PluginComponent plugin={plugin} name={contribution.component} props={{ ticketId }} />
          </PluginBoundary>
        </div>
      ))}
    </>
  );
}

// Literal class names: Tailwind cannot see names built from a template.
const COL_SPAN: Record<number, string> = {
  1: "md:col-span-1",
  2: "md:col-span-2",
  3: "md:col-span-3",
  4: "md:col-span-4",
  5: "md:col-span-5",
  6: "md:col-span-6",
  7: "md:col-span-7",
  8: "md:col-span-8",
  9: "md:col-span-9",
  10: "md:col-span-10",
  11: "md:col-span-11",
  12: "md:col-span-12",
};

/** Defaults of a JSON-schema `properties` object (`{ key: { default } }`). */
function settingsDefaults(schema: Record<string, unknown> | null): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const props = schema?.properties;
  if (typeof props !== "object" || props === null) return out;
  for (const [name, spec] of Object.entries(props)) {
    if (typeof spec === "object" && spec !== null && "default" in spec) {
      out[name] = (spec as { default: unknown }).default;
    }
  }
  return out;
}

/**
 * "Erweiterungen" section on the home page: every widget a plugin registered.
 * Self-contained so the dashboards plan can drop it in favour of its grid.
 */
export function PluginWidgetsSection() {
  const { t } = useTranslation();
  const widgets = useDashboardWidgetDefinitions().filter((w: WidgetDefinition) => w.owner !== "core");
  if (widgets.length === 0) return null;
  return (
    <section data-testid="plugin-widgets">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">{t("plugins.widgetsTitle")}</h2>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
        {widgets.map((w) => {
          const [cols, rows] = w.size.default;
          const Widget = w.component;
          return (
            <div
              key={w.key}
              className={COL_SPAN[Math.min(12, Math.max(1, cols))]}
              data-testid={`plugin-widget-${w.key}`}
            >
              <Widget
                instanceId={`home-${w.key}`}
                settings={settingsDefaults(w.settingsSchema)}
                size={{ w: cols, h: rows }}
                filters={{}}
                mode="view"
              />
            </div>
          );
        })}
      </div>
    </section>
  );
}
