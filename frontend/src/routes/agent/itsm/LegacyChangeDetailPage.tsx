import { useTranslation } from "react-i18next";

/** Route stub: the page is built by a later change-management task (keeps the route and its title). */
export function LegacyChangeDetailPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-5xl p-4" data-testid="legacyChangeDetailPage">
      <h1 className="text-lg font-semibold text-ink">{t("itsm.change.pages.legacyDetail")}</h1>
      <p className="mt-2 text-sm text-muted">{t("itsm.change.stub.body")}</p>
    </div>
  );
}
