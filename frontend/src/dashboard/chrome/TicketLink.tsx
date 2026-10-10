import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { WidgetMode } from "@/plugins/dashboardWidgets";
import { setTicketNavContext } from "@/lib/ticketNavContext";
import { linkTabIndex, rendersLinks } from "./mode";

/** A ticket link inside a widget: not a tab stop in edit mode, plain text in preview. */
export function TicketLink({
  ticketId,
  navIds,
  mode,
  className,
  children,
  testId,
}: {
  ticketId: number;
  /** Ids of the list it is in, for the ticket view's prev/next. */
  navIds?: number[];
  mode: WidgetMode;
  className?: string;
  children: ReactNode;
  testId?: string;
}) {
  const { t } = useTranslation();
  if (!rendersLinks(mode))
    return (
      <span className={className} data-testid={testId}>
        {children}
      </span>
    );
  return (
    <Link
      to="/agent/tickets/$ticketId"
      params={{ ticketId: String(ticketId) }}
      tabIndex={linkTabIndex(mode)}
      onClick={() =>
        setTicketNavContext({ label: t("nav.dashboard"), to: "/agent", ids: navIds ?? [ticketId] })
      }
      className={className}
      data-testid={testId}
    >
      {children}
    </Link>
  );
}
