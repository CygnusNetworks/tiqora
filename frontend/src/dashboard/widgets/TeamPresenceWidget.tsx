import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import type { OnlineAgentOut } from "@/lib/api";
import { Avatar } from "@/components/ui/Avatar";
import { initialsOf } from "@/components/customers/customerFormat";
import { useWidgetData } from "../data";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetDataBody, WidgetEmpty } from "../chrome/WidgetDataBody";

export interface TeamPresenceSettings {
  limit: number;
}

export type PresenceStatus = "available" | "in_chat";

export interface TeamPresenceData {
  online?: (OnlineAgentOut & { status?: PresenceStatus })[];
  total_online: number;
  /** Wallboard without details: counts per status instead of people. */
  by_status?: Partial<Record<PresenceStatus, number>>;
}

/** Who is online right now, and who is in a web chat (no "am Telefon"/"Pause" source yet — Deviation 8). */
export function TeamPresenceWidget({ size, mode }: WidgetProps<TeamPresenceSettings>) {
  const { t } = useTranslation();
  const { status, data } = useWidgetData<TeamPresenceData>();
  const total = status === "ok" && typeof data?.total_online === "number" ? data.total_online : null;
  return (
    <WidgetFrame
      title={t("dashboards.widgets.teamPresence.title")}
      mode={mode}
      testId="team-presence-widget"
      actions={
        total != null && total > 0 ? (
          <span className="shrink-0 text-xs text-muted">{t("dashboards.widgets.teamPresence.online", { count: total })}</span>
        ) : undefined
      }
    >
      <WidgetDataBody<TeamPresenceData> h={size.h}>
        {(d) => {
          const online = Array.isArray(d?.online) ? d.online : [];
          const byStatus = d?.by_status;
          if (online.length === 0 && byStatus && (d?.total_online ?? 0) > 0)
            return (
              <ul className="flex flex-col gap-1.5 text-sm text-ink" data-testid="team-presence-counts">
                <li>{t("dashboards.widgets.teamPresence.countAvailable", { count: byStatus.available ?? 0 })}</li>
                <li>{t("dashboards.widgets.teamPresence.countInChat", { count: byStatus.in_chat ?? 0 })}</li>
              </ul>
            );
          if (online.length === 0) return <WidgetEmpty>{t("dashboards.widgets.teamPresence.empty")}</WidgetEmpty>;
          const more = (d?.total_online ?? online.length) - online.length;
          return (
            <ul className="flex flex-col gap-2.5">
              {online.map((a) => (
                <li key={a.id} className="flex items-center gap-2.5 text-sm">
                  <span className="relative flex shrink-0">
                    <Avatar avatarUrl={a.avatar_url} domainFallback={false} initials={initialsOf(a.full_name || a.login)} size={28} />
                    <span
                      aria-hidden="true"
                      className="absolute -bottom-px -right-px h-2.5 w-2.5 rounded-full border-2 border-surface bg-green"
                    />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-ink">{a.full_name || a.login}</span>
                  {a.status === "in_chat" && (
                    <span className="shrink-0 text-xs text-muted" data-testid={`presence-in-chat-${a.id}`}>
                      {t("dashboards.widgets.teamPresence.inChat")}
                    </span>
                  )}
                </li>
              ))}
              {more > 0 && (
                <li className="text-xs text-muted">{t("dashboards.widgets.teamPresence.more", { count: more })}</li>
              )}
            </ul>
          );
        }}
      </WidgetDataBody>
    </WidgetFrame>
  );
}
