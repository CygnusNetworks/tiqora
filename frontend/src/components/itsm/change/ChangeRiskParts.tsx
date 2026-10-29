import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { SelectField } from "@/components/ui/SelectField";
import type { ChangeRisk, ChangeRiskFinding, ChangeRiskOverrideIn } from "@/lib/changeApi";
import { RISK_LEVELS } from "@/lib/changePhases";
import { RiskBadge } from "./RiskBadge";
import { FIELD_CLASS, refHref, refLabel } from "./changeUi";

const SEVERITY_TONE = { info: "muted", warn: "warn", block: "danger" } as const;
const NO_OVERRIDE = "none";

/** A reference the caller may read: a link where an agent page exists, else its name. */
export function RefLink({ refKey }: { refKey: string }) {
  const { t } = useTranslation();
  const href = refHref(refKey);
  const label = refLabel(refKey, t);
  return href ? (
    <RouterAnchor href={href} className="text-accent hover:underline">
      {label}
    </RouterAnchor>
  ) : (
    <span className="text-ink">{label}</span>
  );
}

export function HiddenCount({ count, testId }: { count: number; testId: string }) {
  const { t } = useTranslation();
  if (count <= 0) return null;
  return (
    <span className="text-xs text-muted" data-testid={testId}>
      {t("itsm.change.hiddenCount", { count })}
    </span>
  );
}

/** What the checks found; the API lists only readable refs and counts the others. */
export function RiskFindingList({ findings }: { findings: ChangeRiskFinding[] }) {
  const { t } = useTranslation();
  if (findings.length === 0) {
    return (
      <p className="text-xs text-muted" data-testid="change-risk-findings-empty">
        {t("itsm.change.risk.findingsEmpty")}
      </p>
    );
  }
  return (
    <ul className="space-y-1.5" data-testid="change-risk-findings">
      {findings.map((f, i) => (
        <li key={`${f.check_key}-${i}`} className="text-xs" data-testid="change-risk-finding" data-check={f.check_key}>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={SEVERITY_TONE[f.severity]}>{t(`itsm.change.conflicts.severity.${f.severity}`)}</Badge>
            <span className="text-ink">{t([f.message_key, "itsm.change.risk.findings.generic"], { ...f.params, check: f.check_key })}</span>
            <span className="tabular-nums text-muted">{t("itsm.change.risk.points", { count: f.points })}</span>
          </div>
          {(f.refs.length > 0 || f.hidden_ref_count > 0) && (
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-1">
              {f.refs.map((ref) => (
                <RefLink key={ref} refKey={ref} />
              ))}
              <HiddenCount count={f.hidden_ref_count} testId="change-risk-hidden-refs" />
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * The manager's own risk level ("Risiko übersteuern") with a reason; "Keine Übersteuerung" goes
 * back to the computed level. Only shown when the API says `can_override`.
 */
export function RiskOverride({
  risk,
  pending,
  onSave,
}: {
  risk: ChangeRisk;
  pending: boolean;
  onSave: (body: ChangeRiskOverrideIn) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<string>(risk.override_level ?? NO_OVERRIDE);
  const [reason, setReason] = useState(risk.override_reason ?? "");

  return (
    <div className="space-y-2" data-testid="change-risk-override">
      {risk.override_level && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
          {t("itsm.change.risk.override.current")} <RiskBadge level={risk.override_level} />
          {risk.override_reason && <span className="break-words text-ink">{risk.override_reason}</span>}
        </p>
      )}
      {risk.can_override && !open && (
        <Button size="sm" onClick={() => setOpen(true)} data-testid="change-risk-override-open">
          {t("itsm.change.risk.override.open")}
        </Button>
      )}
      {open && (
        <div className="space-y-2 rounded border border-hairline bg-surface-subtle p-2">
          <div className="max-w-xs text-xs text-muted">
            <span>{t("itsm.change.risk.override.level")}</span>
            <SelectField<string>
              testId="change-risk-override-level"
              aria-label={t("itsm.change.risk.override.level")}
              className="mt-1"
              items={[
                { value: NO_OVERRIDE, label: t("itsm.change.risk.override.none") },
                ...RISK_LEVELS.map((l) => ({ value: l, label: t(`itsm.change.risk.levels.${l}`) })),
              ]}
              value={level}
              onChange={setLevel}
            />
          </div>
          <label className="block text-xs text-muted">
            {t("itsm.change.risk.override.reason")}
            <textarea
              className={`${FIELD_CLASS} mt-1`}
              rows={2}
              maxLength={1000}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              data-testid="change-risk-override-reason"
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="secondary" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={pending || !reason.trim()}
              onClick={() => onSave({ level: level === NO_OVERRIDE ? null : level, reason: reason.trim() })}
              data-testid="change-risk-override-save"
            >
              {t("common.save")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
