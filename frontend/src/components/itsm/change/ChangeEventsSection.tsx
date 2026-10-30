import { useMemo } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { changeApi, changeKeys, type ChangeEvent } from "@/lib/changeApi";
import { api } from "@/lib/api";
import { changeErrorText, SECTION_CLASS, utcText, warningText } from "./changeUi";

export const EVENTS_PAGE = 50;
/** Writes of the approval outcome path are attributed to the system user (backend ruling F2). */
const SYSTEM_USER_ID = 1;

/** One detail value as plain text (React escapes it; nothing is rendered as HTML). */
function valueText(key: string, value: unknown, t: TFunction): string {
  if (value == null) return "–";
  if (key === "template_warnings" && Array.isArray(value)) return value.map((w) => warningText(String(w), t)).join("\n");
  if (key === "reason" && value === "no_approvers") return t("itsm.change.approval.noApprovers");
  if (key === "status" && typeof value === "string") return t(`approvals.status.${value}`, { defaultValue: value });
  if ((key === "from" || key === "to") && typeof value === "string") return t(`itsm.change.risk.levels.${value}`, { defaultValue: value });
  if (Array.isArray(value)) return value.map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v))).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function EventRow({ event, userName }: { event: ChangeEvent; userName: Map<number, string> }) {
  const { t } = useTranslation();
  const phase = (p: string | null) => (p ? t(`itsm.change.phases.${p}`, { defaultValue: p }) : "–");
  // the first phase event of a change has no previous phase
  const title =
    event.event !== "phase"
      ? t(`itsm.change.events.kinds.${event.event}`, { defaultValue: event.event })
      : event.from_phase
        ? t("itsm.change.events.phase", { from: phase(event.from_phase), to: phase(event.to_phase) })
        : t("itsm.change.events.phaseStart", { to: phase(event.to_phase) });
  const entries = Object.entries(event.detail ?? {});
  return (
    <li className="space-y-0.5 py-1.5 text-xs" data-testid="change-event" data-event={event.event} data-event-id={event.id}>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="tabular-nums text-muted">{utcText(event.created_at)}</span>
        <span className="font-medium text-ink">{title}</span>
        <span className="text-muted">
          {event.created_by === SYSTEM_USER_ID ? t("itsm.change.events.system") : userName.get(event.created_by) ?? t("itsm.change.events.user", { id: event.created_by })}
        </span>
      </div>
      {entries.length > 0 && (
        <dl className="grid gap-x-2 pl-1 text-muted sm:grid-cols-[auto_1fr]">
          {entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt>{t(`itsm.change.events.detail.${key}`, { defaultValue: key })}</dt>
              <dd className="min-w-0 whitespace-pre-wrap break-words text-ink">{valueText(key, value, t)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

/**
 * "Verlauf": the change's own events (phase steps, approval requests and outcomes, overrides,
 * forced steps, risk overrides, tasks, links), newest first, paged backwards with `before_id`.
 * Detail values are shown as plain text.
 */
export function ChangeEventsSection({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const q = useInfiniteQuery({
    queryKey: changeKeys.events(ticketId),
    queryFn: ({ pageParam, signal }) => changeApi.events(ticketId, { limit: EVENTS_PAGE, before_id: pageParam }, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length < EVENTS_PAGE ? undefined : last[last.length - 1].id),
  });
  const events = q.data?.pages.flat() ?? [];
  // Names of the acting agents; ids the list does not know (a former agent) keep "Benutzer #n".
  const agentsQ = useQuery({ queryKey: ["reference", "agents"], queryFn: ({ signal }) => api.listReferenceAgents(signal), staleTime: 60_000 });
  const userName = useMemo(() => new Map((agentsQ.data ?? []).map((a) => [a.id, a.full_name || a.login])), [agentsQ.data]);

  return (
    <section className={SECTION_CLASS} aria-label={t("itsm.change.events.title")} data-testid="change-events">
      <h3 className="text-sm font-semibold text-ink">{t("itsm.change.events.title")}</h3>
      {q.isLoading && <Spinner />}
      {q.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="change-events-error">
          {changeErrorText(q.error, t)}
        </p>
      )}
      {q.isSuccess && events.length === 0 && <p className="text-xs text-muted">{t("itsm.change.events.empty")}</p>}
      {events.length > 0 && (
        <ol className="divide-y divide-hairline">
          {events.map((e) => (
            <EventRow key={e.id} event={e} userName={userName} />
          ))}
        </ol>
      )}
      {q.hasNextPage && (
        <Button size="sm" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()} data-testid="change-events-more">
          {t("itsm.change.events.more")}
        </Button>
      )}
    </section>
  );
}
