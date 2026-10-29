import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";

/** Source badge of the Znuny changes: live from Znuny (Mode A) or the archive in Tiqora (Mode B). */
export function LegacySourceBadge({ source }: { source: string }) {
  const { t } = useTranslation();
  const live = source === "live";
  return (
    <Badge tone={live ? "accent" : "muted"} data-testid="legacy-source-badge" data-source={source}>
      {t(live ? "itsm.change.legacy.sourceLive" : "itsm.change.legacy.sourceArchive")}
    </Badge>
  );
}
