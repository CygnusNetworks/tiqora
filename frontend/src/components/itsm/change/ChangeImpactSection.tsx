import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { JobProgress } from "@/components/jobs/JobProgress";
import { useJob } from "@/components/jobs/useJob";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { Spinner } from "@/components/ui/Spinner";
import { changeApi, changeKeys, type Change } from "@/lib/changeApi";
import { ciDetailHref, type CiSource } from "@/lib/itsmRoutes";
import { HiddenCount } from "./ChangeRefs";
import { changeErrorText, FINAL_PHASES, SECTION_CLASS, useChangeRefresh, utcText } from "./changeUi";

const FUNCTION_TONE: Record<string, "success" | "warn" | "danger"> = { operational: "success", warning: "warn", incident: "danger" };

function Functionality({ value, label }: { value: string | null; label?: string | null }) {
  const { t } = useTranslation();
  if (!value) return null;
  return <Badge tone={FUNCTION_TONE[value] ?? "default"}>{label || t(`itsm.change.impact.functionality.${value}`, { defaultValue: value })}</Badge>;
}

function Direct({ direct }: { direct: boolean }) {
  const { t } = useTranslation();
  return <span className="text-muted">{direct ? t("itsm.change.impact.direct") : t("itsm.change.impact.indirect")}</span>;
}

/**
 * "Auswirkung": the services and CIs the change touches (linked, or reached through the CMDB) and
 * the open incidents on those services. Unreadable CIs and services are only counted. The set is
 * computed by a background job; "Neu berechnen" starts it and the section reloads when it is done
 * (the risk and the conflicts read the same set, so they reload too).
 */
export function ChangeImpactSection({ change }: { change: Change }) {
  const { t } = useTranslation();
  const id = change.ticket_id;
  const refresh = useChangeRefresh(id);
  const [jobId, setJobId] = useState<number | null>(null);
  const q = useQuery({ queryKey: changeKeys.impact(id), queryFn: ({ signal }) => changeApi.impact(id, signal) });
  const { job, isTerminal } = useJob(jobId);
  const recompute = useMutation({ mutationFn: () => changeApi.recomputeImpact(id), onSuccess: (r) => setJobId(r.job_id) });

  useEffect(() => {
    if (jobId != null && isTerminal) refresh();
  }, [jobId, isTerminal, refresh]);

  const impact = q.data;
  const running = jobId != null && !isTerminal;
  const canRecompute = change.can_write && !FINAL_PHASES.has(change.phase);
  const error = recompute.error ?? q.error;

  return (
    <section className={SECTION_CLASS} aria-label={t("itsm.change.impact.title")} data-testid="change-impact">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">{t("itsm.change.impact.title")}</h3>
        {canRecompute && (
          <Button size="sm" disabled={running || recompute.isPending} onClick={() => recompute.mutate()} data-testid="change-impact-recompute">
            {t("itsm.change.impact.recompute")}
          </Button>
        )}
      </div>
      {jobId != null && (job ? <JobProgress job={job} testId="change-impact-job" /> : <Spinner />)}
      {error && (
        <p className="text-xs text-danger" role="alert" data-testid="change-impact-error">
          {changeErrorText(error, t)}
        </p>
      )}
      {q.isLoading && <Spinner />}
      {impact && (
        <div className="space-y-3 text-xs">
          <p className="text-muted">
            {impact.computed_at ? t("itsm.change.impact.computedAt", { time: utcText(impact.computed_at) }) : null}
          </p>
          {(impact.stale || !impact.computed_at) && (
            <p className="text-escalation" data-testid="change-impact-stale">
              {impact.computed_at
                ? t("itsm.change.impact.stale")
                : impact.requested_at
                  ? t("itsm.change.impact.computing")
                  : t("itsm.change.impact.notComputed")}
            </p>
          )}
          {impact.truncated && (
            <p className="text-escalation" data-testid="change-impact-truncated">
              {t("itsm.change.impact.truncated")}
            </p>
          )}
          <div data-testid="change-impact-services">
            <h4 className="font-medium text-muted">{t("itsm.change.impact.services")}</h4>
            {impact.services.length === 0 && impact.hidden_service_count === 0 && <p className="text-muted">{t("itsm.change.impact.none")}</p>}
            <ul className="space-y-0.5">
              {impact.services.map((s) => (
                <li key={s.service_id} className="flex flex-wrap items-center gap-2" data-testid="change-impact-service">
                  <RouterAnchor href={`/agent/services?service_id=${s.service_id}`} className="text-accent hover:underline">
                    {s.name}
                  </RouterAnchor>
                  <Direct direct={s.direct} />
                  <Functionality value={s.incident_functionality} />
                </li>
              ))}
            </ul>
            <HiddenCount count={impact.hidden_service_count} testId="change-impact-hidden-services" />
          </div>
          <div data-testid="change-impact-cis">
            <h4 className="font-medium text-muted">{t("itsm.change.impact.cis")}</h4>
            {impact.cis.length === 0 && impact.hidden_ci_count === 0 && <p className="text-muted">{t("itsm.change.impact.none")}</p>}
            <ul className="space-y-0.5">
              {impact.cis.map((ci) => (
                <li key={ci.ref} className="flex flex-wrap items-center gap-2" data-testid="change-impact-ci">
                  <RouterAnchor href={ciDetailHref(ci.ref.split(":")[0] as CiSource, ci.ci_id)} className="min-w-0 break-words text-accent hover:underline">
                    <span className="font-mono">{ci.number}</span> {ci.name}
                  </RouterAnchor>
                  <span className="text-muted">{ci.class_name}</span>
                  <Direct direct={ci.direct} />
                  <Functionality value={ci.incident_functionality} label={ci.incident_state} />
                </li>
              ))}
            </ul>
            <HiddenCount count={impact.hidden_ci_count} testId="change-impact-hidden-cis" />
          </div>
          <div data-testid="change-impact-incidents">
            <h4 className="font-medium text-muted">{t("itsm.change.impact.incidents")}</h4>
            {impact.incidents.length === 0 ? (
              <p className="text-muted">{t("itsm.change.impact.noIncidents")}</p>
            ) : (
              <ul className="space-y-0.5">
                {impact.incidents.map((inc) => (
                  <li key={inc.ticket_id} className="flex flex-wrap items-center gap-2">
                    <RouterAnchor href={`/agent/tickets/${inc.ticket_id}`} className="min-w-0 break-words text-accent hover:underline">
                      <span className="font-mono">{inc.tn}</span> {inc.title}
                    </RouterAnchor>
                    <span className="text-muted">{inc.state}</span>
                  </li>
                ))}
              </ul>
            )}
            {impact.incidents_truncated && <p className="text-muted">{t("itsm.change.impact.incidentsTruncated")}</p>}
          </div>
        </div>
      )}
    </section>
  );
}
