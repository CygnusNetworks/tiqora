import { useTranslation } from "react-i18next";

/** `/admin/itsm/link-types`: link-type editor. Filled in by the ITSM admin task; this is the routed shell. */
export function LinkTypesPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-itsm-link-types-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("admin.itsm.linkTypes.title")}</h1>
    </div>
  );
}
