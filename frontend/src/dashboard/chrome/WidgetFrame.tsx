import { createContext, useContext, useId, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import type { WidgetMode } from "@/plugins/dashboardWidgets";
import { PinIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { useTranslation } from "react-i18next";
import { useDashboardFilters } from "../filters/filtersContext";
import { DRILL_PATH, type DrillSearch } from "../drill/drillLinks";
import { useIsKiosk } from "../wallboard/kioskContext";
import { isInteractive, linkTabIndex, rendersLinks } from "./mode";

/**
 * What the host knows about a placed widget that the shared `WidgetProps` do not carry. Core
 * widgets render their own `WidgetFrame`; the host (dashboard grid, edit mode, settings preview)
 * injects these through `WidgetChromeProvider`. Every field is optional with a safe default.
 */
export interface WidgetChrome {
  /** The widget's stored `title_override`; wins over the widget's own title. */
  titleOverride?: string | null;
  /** The viewer may edit this dashboard (empty states then offer their action). */
  canEdit?: boolean;
  /** Edit mode: the host's header buttons (Einstellungen / Duplizieren / Entfernen). */
  actions?: ReactNode;
  /** Edit mode: the host's grab handle, before the title. */
  handle?: ReactNode;
  /** Opens this widget's settings (empty-state actions such as "Filter ändern"). */
  onConfigure?: () => void;
  /** The widget ignores the dashboard-wide filters (shows the pin glyph while filters are active). */
  pinned?: boolean;
  /** The dashboard this widget is placed on (widgets that start work for themselves, e.g. report jobs). */
  dashboardId?: number;
}

const ChromeContext = createContext<WidgetChrome>({});

export function WidgetChromeProvider({ value, children }: { value: WidgetChrome; children: ReactNode }) {
  return <ChromeContext.Provider value={value}>{children}</ChromeContext.Provider>;
}

export function useWidgetChrome(): WidgetChrome {
  return useContext(ChromeContext);
}

/** The accent link right of the title ("Alle 12 anzeigen"). */
export type FrameLink =
  | { label: string; to: "/agent/queues" }
  | { label: string; to: "/agent/calendar" }
  | { label: string; to: typeof DRILL_PATH; search: DrillSearch }
  | { label: string; to: "/agent/searches/$searchId"; params: { searchId: string } };

const LINK_CLASS =
  "shrink-0 text-sm text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

function HeaderLink({ link, mode }: { link: FrameLink; mode: WidgetMode }) {
  const kiosk = useIsKiosk();
  if (!rendersLinks(mode, kiosk)) return null;
  const tabIndex = linkTabIndex(mode);
  const inert = !isInteractive(mode) && "pointer-events-none";
  if (link.to === "/agent/calendar")
    return (
      <Link to="/agent/calendar" tabIndex={tabIndex} className={cn(LINK_CLASS, inert)}>
        {link.label}
      </Link>
    );
  if (link.to === DRILL_PATH)
    return (
      <Link to={DRILL_PATH} search={link.search} tabIndex={tabIndex} className={cn(LINK_CLASS, inert)}>
        {link.label}
      </Link>
    );
  if (link.to === "/agent/searches/$searchId")
    return (
      <Link to="/agent/searches/$searchId" params={link.params} tabIndex={tabIndex} className={cn(LINK_CLASS, inert)}>
        {link.label}
      </Link>
    );
  return (
    <Link to="/agent/queues" tabIndex={tabIndex} className={cn(LINK_CLASS, inert)}>
      {link.label}
    </Link>
  );
}

/**
 * The card around a widget (mockup: 14 px radius, hairline border, title row 15 px / 600 with an
 * optional accent link on the right). `variant="kpi"` is the counter tile's muted label row.
 * Outside view mode the body is inert: no tab stops, no pointer events.
 */
export function WidgetFrame({
  title,
  link,
  actions,
  mode,
  children,
  testId,
  variant = "default",
  scaleBody = true,
}: {
  title: string;
  link?: FrameLink;
  /** Widget-owned header extras (e.g. "2 online", a delta). */
  actions?: ReactNode;
  mode: WidgetMode;
  children: ReactNode;
  testId: string;
  variant?: "default" | "kpi";
  /** Wallboard: scale the body with the board (`zoom`); widgets with their own wallboard type scale pass false. */
  scaleBody?: boolean;
}) {
  const { t } = useTranslation();
  const chrome = useWidgetChrome();
  const { active: filtersActive } = useDashboardFilters();
  const headingId = useId();
  const interactive = isInteractive(mode);
  const editing = mode === "edit";
  const hostActions = editing ? chrome.actions : undefined;
  const shownTitle = chrome.titleOverride?.trim() || title;
  const board = mode === "wallboard";
  return (
    <article
      data-testid={testId}
      aria-labelledby={headingId}
      className={cn(
        "relative flex h-full min-h-0 min-w-0 flex-col border border-hairline bg-surface",
        board ? "gap-3 overflow-hidden rounded-[20px] px-[30px] py-[26px]" : "gap-2 rounded-[14px] px-[18px] py-4",
      )}
    >
      <header className="flex min-w-0 items-center justify-between gap-2">
        {editing && chrome.handle}
        <h2
          id={headingId}
          className={cn(
            "truncate",
            // KPI label: never gives way to the header extras (the delta truncates instead)
            board
              ? "min-w-0 flex-1 text-[length:calc(22px*var(--wb-zoom,1))] font-medium leading-tight text-muted"
              : variant === "kpi"
                ? cn("text-sm font-medium text-muted", hostActions ? "min-w-0 flex-1" : "max-w-full shrink-0")
                : "min-w-0 flex-1 text-[15px] font-semibold text-ink",
          )}
        >
          {shownTitle}
        </h2>
        {chrome.pinned && filtersActive && (
          <span
            role="img"
            aria-label={t("dashboards.filters.pinned")}
            title={t("dashboards.filters.pinned")}
            data-testid="widget-pinned"
            className="shrink-0 text-muted"
          >
            <PinIcon className="h-3.5 w-3.5" />
          </span>
        )}
        {hostActions ?? (
          <>
            {actions}
            {link && <HeaderLink link={link} mode={mode} />}
          </>
        )}
      </header>
      <div
        data-testid="widget-body"
        inert={!interactive}
        data-wallboard-scaled={board && scaleBody ? "true" : undefined}
        style={board && scaleBody ? { zoom: "var(--wb-zoom, 1)" } : undefined}
        className={cn("min-h-0 flex-1", board ? "overflow-hidden" : "overflow-auto", !interactive && "pointer-events-none select-none")}
      >
        {children}
      </div>
    </article>
  );
}
