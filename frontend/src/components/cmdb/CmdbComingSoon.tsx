import { useTranslation } from "react-i18next";

/** Placeholder for CMDB pages that later tasks replace; keeps every registered route reachable. */
export function CmdbComingSoon() {
  const { t } = useTranslation();
  return (
    <div className="p-6 text-sm text-muted" data-testid="cmdb-coming-soon">
      {t("cmdb.comingSoon")}
    </div>
  );
}
