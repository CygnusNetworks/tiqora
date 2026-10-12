import { useQuery } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { CmdbModeNotice } from "@/components/cmdb/CmdbModeNotice";
import { ImportWizard } from "@/components/cmdb/ImportWizard";
import { Spinner } from "@/components/ui/Spinner";
import { cmdbApi, useCmdbStatus } from "@/lib/cmdbApi";

/** `/agent/cmdb/import`: CSV/XLSX import into one class (wizard with dry run and mapping templates). */
export function CmdbImportPage() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/agent/cmdb/import" }) as { class?: number };
  const statusQ = useCmdbStatus();
  const active = statusQ.data?.mode === "tiqora";
  const classesQ = useQuery({ queryKey: ["cmdb", "classes"], queryFn: () => cmdbApi.classes(), enabled: active, staleTime: 60_000 });
  const writable = (classesQ.data ?? []).filter((c) => c.valid && c.can_write);

  const shell = (children: React.ReactNode) => (
    <div className="mx-auto w-full min-w-0 max-w-4xl space-y-4 p-4 sm:p-6" data-testid="cmdb-import-page">
      {children}
    </div>
  );

  if (statusQ.data && !active) return shell(<CmdbModeNotice status={statusQ.data} />);
  if (!statusQ.data || classesQ.isPending) return shell(<Spinner />);
  if (classesQ.isError) {
    return shell(
      <p role="alert" className="text-sm text-danger" data-testid="cmdb-import-load-failed">
        {t("cmdb.import.loadFailed")}
      </p>,
    );
  }

  return shell(
    <>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="font-display text-xl font-bold text-ink">{t("cmdb.import.title")}</h1>
        <Link to="/agent/cmdb" className="text-sm text-accent hover:underline" data-testid="cmdb-import-back">
          {t("cmdb.detail.back")}
        </Link>
      </div>
      {writable.length === 0 ? (
        <p className="text-sm text-muted" data-testid="cmdb-import-no-classes">
          {t("cmdb.import.noWritableClasses")}
        </p>
      ) : (
        <ImportWizard classes={writable} initialClassId={writable.some((c) => c.id === search.class) ? search.class : undefined} />
      )}
    </>,
  );
}
