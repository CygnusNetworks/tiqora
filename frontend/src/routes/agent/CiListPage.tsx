import { useTranslation } from "react-i18next";

/** `/agent/itsm/cis`: the CI list. Filled in by the CI pages task; this is the routed shell. */
export function CiListPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="ci-list-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("itsm.ci.title")}</h1>
    </div>
  );
}
