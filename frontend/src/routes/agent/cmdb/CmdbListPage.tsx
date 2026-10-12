import { useTranslation } from "react-i18next";
import { CmdbModeNotice } from "@/components/cmdb/CmdbModeNotice";
import { useCmdbStatus } from "@/lib/cmdbApi";

/** Placeholder list page: the mode notice, or "Noch keine Klassen" until Task 22 fills it. */
export function CmdbListPage() {
  const { t } = useTranslation();
  const q = useCmdbStatus();
  const status = q.data;
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6" data-testid="cmdb-list-page">
      <h1 className="font-display text-xl font-bold text-ink">{t("cmdb.title")}</h1>
      {status && status.mode !== "tiqora" ? (
        <CmdbModeNotice status={status} />
      ) : (
        status && <p className="text-sm text-muted">{t("cmdb.noClasses")}</p>
      )}
    </div>
  );
}
