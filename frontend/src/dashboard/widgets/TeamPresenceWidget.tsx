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

export interface TeamPresenceData {
  online: OnlineAgentOut[];
  total_online: number;
}

/** Who is online right now (presence has no "am Telefon"/"Pause" yet — Deviation 8). */
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
