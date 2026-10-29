import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { isRiskLevel } from "@/lib/changePhases";

const TONE = { low: "success", medium: "default", high: "warn", very_high: "danger" } as const;

/** Risk level chip of a change; `level` is the effective level (manager override applied). */
export function RiskBadge({ level, className }: { level: string | null | undefined; className?: string }) {
  const { t } = useTranslation();
  if (!isRiskLevel(level)) {
    return (
      <Badge tone="muted" className={className} data-testid="risk-badge" data-risk-level="none">
        {t("itsm.change.risk.none")}
      </Badge>
    );
  }
  return (
    <Badge tone={TONE[level]} className={className} data-testid="risk-badge" data-risk-level={level}>
      {t("itsm.change.risk.levels." + level)}
    </Badge>
  );
}
