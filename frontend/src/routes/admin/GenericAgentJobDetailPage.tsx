import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "@tanstack/react-router";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import {
  type DecodedEntry,
  decodeJob,
  scheduleSummary,
} from "@/lib/genericAgentJob";

/** minutes → compact human duration ("30 Tage" / "12 Std." / "90 Min."). */
function humanizeMinutes(raw: string): string | null {
  const m = Number.parseInt(raw, 10);
  if (!Number.isFinite(m)) return null;
  if (m % (60 * 24) === 0) return `${m / (60 * 24)} Tage`;
  if (m % 60 === 0) return `${m / 60} Std.`;
  return `${m} Min.`;
}

export function GenericAgentJobDetailPage() {
  const { t } = useTranslation();
  const { jobName } = useParams({ from: "/admin/generic-agent-jobs/$jobName" });

  const detailQ = useQuery({
    queryKey: ["admin", "generic-agent-jobs", jobName],
    queryFn: ({ signal }) => api.getGenericAgentJob(jobName, signal),
  });
  const queuesQ = useQuery({
    queryKey: ["reference", "queues"],
    queryFn: ({ signal }) => api.listReferenceQueues({}, signal),
  });
  const statesQ = useQuery({
    queryKey: ["reference", "states"],
    queryFn: ({ signal }) => api.listReferenceStates(signal),
  });
  const prioritiesQ = useQuery({
    queryKey: ["reference", "priorities"],
    queryFn: ({ signal }) => api.listReferencePriorities(signal),
  });
  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: ({ signal }) => api.listReferenceAgents(signal),
  });

  const nameMaps = useMemo(() => {
    const byId = <T,>(rows: T[] | undefined, id: (r: T) => number, label: (r: T) => string) => {
      const map = new Map<number, string>();
      for (const r of rows ?? []) map.set(id(r), label(r));
      return map;
    };
    return {
      queues: byId(queuesQ.data, (q) => q.id, (q) => q.name),
      states: byId(statesQ.data, (s) => s.id, (s) => s.name),
      priorities: byId(prioritiesQ.data, (p) => p.id, (p) => p.name),
      agents: byId(agentsQ.data, (a) => a.id, (a) => a.full_name),
    };
  }, [queuesQ.data, statesQ.data, prioritiesQ.data, agentsQ.data]);

  const job = detailQ.data ? decodeJob(detailQ.data.settings) : null;

  const weekdays = t("admin.genericAgentJobs.weekdaysShort", {
    returnObjects: true,
  }) as string[];
  const schedule = job
    ? scheduleSummary(job, weekdays, {
        daily: t("admin.genericAgentJobs.daily"),
        hourly: t("admin.genericAgentJobs.hourly"),
        every: t("admin.genericAgentJobs.every"),
      })
    : null;

  // Resolve one entry's raw values to human-readable text where we can.
  const humanizeValues = (key: string, values: string[]): string => {
    const resolve = (map: Map<number, string>) =>
      values
        .map((v) => map.get(Number.parseInt(v, 10)) ?? `#${v}`)
        .join(", ");
    switch (key) {
      case "StateIDs":
      case "StateID":
        return resolve(nameMaps.states);
      case "QueueIDs":
      case "QueueID":
        return resolve(nameMaps.queues);
      case "PriorityIDs":
      case "PriorityID":
        return resolve(nameMaps.priorities);
      case "OwnerIDs":
      case "OwnerID":
        return resolve(nameMaps.agents);
      default:
        if (key.endsWith("OlderMinutes") || key.endsWith("NewerMinutes")) {
          const dur = humanizeMinutes(values[0] ?? "");
          if (dur) {
            const tmpl = key.endsWith("OlderMinutes")
              ? "admin.genericAgentJobs.olderThan"
              : "admin.genericAgentJobs.newerThan";
            return t(tmpl, { duration: dur });
          }
        }
        return values.join(", ");
    }
  };

  const renderEntry = (entry: DecodedEntry) => {
    const human = humanizeValues(entry.key, entry.values);
    const rawJoined = entry.values.join(", ");
    const showRaw = rawJoined && rawJoined !== human;
    return (
      <div
        key={entry.rawKey}
        className={cn(
          "grid grid-cols-[minmax(0,190px)_1fr_auto] items-center gap-x-3 gap-y-1 border-t border-hairline px-3.5 py-2.5 first:border-t-0",
          !entry.executed && "opacity-70",
        )}
        data-testid={`generic-agent-job-entry-${entry.rawKey}`}
      >
        <span className="truncate font-mono text-xs text-ink" title={entry.rawKey}>
          {t(`admin.genericAgentJobs.keyLabel.${entry.key}`, { defaultValue: entry.key })}
        </span>
        <span className="min-w-0 text-sm text-ink">
          <span className="break-words">{human}</span>
          {showRaw && (
            <span className="ml-2 font-mono text-[11px] text-muted">({rawJoined})</span>
          )}
        </span>
        <Badge tone={entry.executed ? "success" : "muted"}>
          {entry.executed
            ? t("admin.genericAgentJobs.executed")
            : t("admin.genericAgentJobs.ignored")}
        </Badge>
      </div>
    );
  };

  const group = (titleKey: string, entries: DecodedEntry[], emptyKey: string) => (
    <div className="overflow-hidden rounded-xl border border-hairline bg-surface">
      <div className="flex items-center justify-between border-b border-hairline bg-surface-subtle px-3.5 py-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">
          {t(titleKey)}
        </span>
      </div>
      {entries.length > 0 ? (
        entries.map(renderEntry)
      ) : (
        <p className="px-3.5 py-2.5 text-sm text-muted">{t(emptyKey)}</p>
      )}
    </div>
  );

  const allActions = job ? [...job.actions, ...job.dynamicFields] : [];

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4" data-testid="admin-generic-agent-job-detail-page">
      <Link to="/admin/generic-agent-jobs" className="text-sm text-accent hover:underline">
        ← {t("common.back")}
      </Link>
      <div className="flex items-center gap-3">
        <h1 className="font-display text-xl font-semibold text-ink">{jobName}</h1>
        {job && (
          <Badge tone={job.valid ? "success" : "muted"}>
            {job.valid ? t("admin.genericAgentJobs.active") : t("admin.genericAgentJobs.inactive")}
          </Badge>
        )}
        <Badge tone="muted">{t("admin.readOnly")}</Badge>
      </div>

      {detailQ.isLoading || !job ? (
        <div className="flex justify-center p-6">
          <Spinner />
        </div>
      ) : (
        <div className="space-y-3">
          {/* Schedule */}
          <div className="overflow-hidden rounded-xl border border-hairline bg-surface">
            <div className="flex items-center justify-between border-b border-hairline bg-surface-subtle px-3.5 py-2">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">
                {t("admin.genericAgentJobs.schedule")}
              </span>
              {job.hasSchedule && (
                <Badge tone="success">{t("admin.genericAgentJobs.runsAutomatically")}</Badge>
              )}
            </div>
            <p className="px-3.5 py-2.5 text-sm text-ink">
              {schedule ?? (
                <span className="italic text-muted">{t("admin.genericAgentJobs.manual")}</span>
              )}
            </p>
          </div>

          {group("admin.genericAgentJobs.criteria", job.criteria, "admin.genericAgentJobs.noCriteria")}
          {group("admin.genericAgentJobs.actions", allActions, "admin.genericAgentJobs.noActions")}

          <p className="px-1 text-xs text-muted">{t("admin.genericAgentJobs.ignoredHint")}</p>
        </div>
      )}
    </div>
  );
}
