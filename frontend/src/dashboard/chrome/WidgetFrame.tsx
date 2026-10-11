import { createContext, useContext, useId, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import type { WidgetMode } from "@/plugins/dashboardWidgets";
import { cn } from "@/lib/cn";
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
}

const ChromeContext = createContext<WidgetChrome>({});

export function WidgetChromeProvider({ value, children }: { value: WidgetChrome; children: ReactNode }) {
  return <ChromeContext.Provider value={value}>{children}</ChromeContext.Provider>;
}

export function useWidgetChrome(): WidgetChrome {
  return useContext(ChromeContext);
}

/** Search params of the queue view a header link may open. */
export interface QueueLinkSearch {
  queue_id?: number;
  owner_id?: number;
  state_type?: "new";
  escalated?: true;
}

/** The accent link right of the title ("Alle 12 anzeigen"). */
export type FrameLink =
  | { label: string; to: "/agent/queues"; search?: QueueLinkSearch }
  | { label: string; to: "/agent/calendar" }
  | { label: string; to: "/agent/searches/$searchId"; params: { searchId: string } };

const LINK_CLASS =
  "shrink-0 text-sm text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

function HeaderLink({ link, mode }: { link: FrameLink; mode: WidgetMode }) {
  if (!rendersLinks(mode)) return null;
  const tabIndex = linkTabIndex(mode);
  const inert = !isInteractive(mode) && "pointer-events-none";
  if (link.to === "/agent/calendar")
    return (
      <Link to="/agent/calendar" tabIndex={tabIndex} className={cn(LINK_CLASS, inert)}>
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
    <Link to="/agent/queues" search={link.search ?? {}} tabIndex={tabIndex} className={cn(LINK_CLASS, inert)}>
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
}: {
  title: string;
  link?: FrameLink;
  /** Widget-owned header extras (e.g. "2 online", a delta). */
  actions?: ReactNode;
  mode: WidgetMode;
  children: ReactNode;
  testId: string;
  variant?: "default" | "kpi";
}) {
  const chrome = useWidgetChrome();
  const headingId = useId();
  const interactive = isInteractive(mode);
  const editing = mode === "edit";
  const hostActions = editing ? chrome.actions : undefined;
  const shownTitle = chrome.titleOverride?.trim() || title;
  return (
    <article
      data-testid={testId}
      aria-labelledby={headingId}
      className="flex h-full min-h-0 min-w-0 flex-col gap-2 rounded-[14px] border border-hairline bg-surface px-[18px] py-4"
    >
      <header className="flex min-w-0 items-center justify-between gap-2">
        {editing && chrome.handle}
        <h2
          id={headingId}
          className={cn(
            "truncate",
            // KPI label: never gives way to the header extras (the delta truncates instead)
            variant === "kpi"
              ? cn("text-sm font-medium text-muted", hostActions ? "min-w-0 flex-1" : "max-w-full shrink-0")
              : "min-w-0 flex-1 text-[15px] font-semibold text-ink",
          )}
        >
          {shownTitle}
        </h2>
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
        className={cn("min-h-0 flex-1 overflow-auto", !interactive && "pointer-events-none select-none")}
      >
        {children}
      </div>
    </article>
  );
}
