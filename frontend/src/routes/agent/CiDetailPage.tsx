import { useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/** `/agent/itsm/cis/$source/$ciId`: one CI. Filled in by the CI pages task; this is the routed shell. */
export function CiDetailPage() {
  const { t } = useTranslation();
  const { source, ciId } = useParams({ from: "/agent/itsm/cis/$source/$ciId" });
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="ci-detail-page" data-ci={`${source}:${ciId}`}>
      <h1 className="font-display text-xl font-semibold text-ink">{t("itsm.ci.detailTitle")}</h1>
    </div>
  );
}
