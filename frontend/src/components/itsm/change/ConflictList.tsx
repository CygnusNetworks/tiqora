import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { changeApi, changeKeys, type ChangeConflict } from "@/lib/changeApi";
import { RefLink } from "./ChangeRiskParts";
import { changeErrorText, SECTION_CLASS, windowText } from "./changeUi";

const SEVERITY_TONE = { info: "muted", warn: "warn", block: "danger" } as const;

function Shared({ conflict }: { conflict: ChangeConflict }) {
  const { t } = useTranslation();
  const parts = [
    conflict.shared_service_count > 0 ? t("itsm.change.conflicts.sharedServices", { count: conflict.shared_service_count }) : null,
    conflict.shared_ci_count > 0 ? t("itsm.change.conflicts.sharedCis", { count: conflict.shared_ci_count }) : null,
  ].filter(Boolean);
  return parts.length > 0 ? <span className="text-muted">{parts.join(", ")}</span> : null;
}

/**
 * "Konflikte": freeze windows, other changes in the same window on the same services or CIs, tasks
 * outside the change window. A conflict with a change the agent cannot read keeps its kind and
 * counts but names nothing.
 */
export function ConflictList({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: changeKeys.conflicts(ticketId), queryFn: ({ signal }) => changeApi.conflicts(ticketId, signal) });
  const conflicts = q.data ?? [];

  return (
    <section className={SECTION_CLASS} aria-label={t("itsm.change.conflicts.title")} data-testid="change-conflicts">
      <h3 className="text-sm font-semibold text-ink">{t("itsm.change.conflicts.title")}</h3>
      {q.isLoading && <Spinner />}
      {q.isError && (
        <p className="text-xs text-danger" role="alert" data-testid="change-conflicts-error">
          {changeErrorText(q.error, t)}
        </p>
      )}
      {q.isSuccess && conflicts.length === 0 && (
        <p className="text-xs text-muted" data-testid="change-conflicts-empty">
          {t("itsm.change.conflicts.empty")}
        </p>
      )}
      {conflicts.length > 0 && (
        <ul className="space-y-1.5 text-xs">
          {conflicts.map((c, i) => (
            <li key={`${c.kind}-${c.ref ?? i}`} className="space-y-0.5" data-testid="change-conflict" data-kind={c.kind} data-severity={c.severity}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={SEVERITY_TONE[c.severity]}>{t(`itsm.change.conflicts.severity.${c.severity}`)}</Badge>
                {/* a freeze is named by its ref ("Sperrzeitraum #1"); the other kinds lead into theirs */}
                {(c.hidden || !c.ref || c.kind !== "freeze") && (
                  <span className="font-medium text-ink">
                    {c.hidden ? t("itsm.change.conflicts.hidden") : t(`itsm.change.conflicts.kinds.${c.kind}`, { defaultValue: c.kind })}
                  </span>
                )}
                {!c.hidden && c.ref && <RefLink refKey={c.ref} />}
              </div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 pl-1">
                {windowText(c.start, c.end) && <span className="text-muted">{windowText(c.start, c.end)}</span>}
                <Shared conflict={c} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
