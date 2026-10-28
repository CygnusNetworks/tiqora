import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";

type Tone = "default" | "accent" | "warn" | "danger" | "success" | "muted";

/** Badge tone per approval, step or approver status. */
const TONES: Record<string, Tone> = {
  pending: "accent",
  active: "accent",
  approved: "success",
  rejected: "danger",
  cancelled: "muted",
  waiting: "muted",
  forwarded: "muted",
  removed: "muted",
  not_needed: "muted",
};

/** Status of an approval request, a step or an approver; `overdue` adds a danger chip next to it. */
export function ApprovalStatusBadge({ status, overdue = false }: { status: string; overdue?: boolean }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex flex-wrap items-center gap-1" data-testid="approval-status">
      <Badge tone={TONES[status] ?? "default"} data-status={status} data-testid="approval-status-badge">
        {t(`approvals.status.${status}`, { defaultValue: status })}
      </Badge>
      {overdue && (
        <Badge tone="danger" data-testid="approval-overdue-badge">
          {t("approvals.overdue")}
        </Badge>
      )}
    </span>
  );
}
