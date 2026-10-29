import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import type { ChangeType } from "@/lib/changeApi";

const TONE = { standard: "muted", normal: "default", emergency: "danger" } as const;

/** Chip with the change type ("Standard-Change", "Normaler Change", "Notfall-Change"). */
export function ChangeTypeBadge({ type, className }: { type: ChangeType; className?: string }) {
  const { t } = useTranslation();
  return (
    <Badge tone={TONE[type]} className={className} data-testid="change-type-badge" data-change-type={type}>
      {t(`itsm.change.types.${type}`)}
    </Badge>
  );
}
