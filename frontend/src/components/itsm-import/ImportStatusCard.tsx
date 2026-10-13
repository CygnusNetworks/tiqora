import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { StatTile } from "@/components/agent/stats/StatTile";
import { formatDateTime } from "@/lib/format";
import type { ImportStatus } from "@/lib/itsmImportApi";

const MODE_KEY: Record<string, string> = {
  znuny_readthrough: "admin.itsmImport.mode.znuny",
  tiqora: "admin.itsmImport.mode.tiqora",
  off: "admin.itsmImport.mode.off",
};

/** Mode, Znuny ITSM detection, Znuny-side counts and the last run. */
export function ImportStatusCard({ status }: { status: ImportStatus }) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.resolvedLanguage ?? i18n.language);
  const num = (n: number) => new Intl.NumberFormat(locale).format(n);
  const counts = status.znuny_counts;
  const last = status.last_run;

  return (
    <section className="space-y-3 rounded-xl border border-hairline bg-surface p-4" data-testid="itsm-import-status">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
        <p>
          <span className="text-muted">{t("admin.itsmImport.modeLabel")}: </span>
          <span className="font-semibold text-ink" data-testid="itsm-import-mode">
            {t(MODE_KEY[status.mode] ?? "admin.itsmImport.mode.off")}
          </span>
        </p>
        <p data-testid="itsm-import-detection">
          {status.znuny_itsm_present
            ? t("admin.itsmImport.znunyFound", { profile: status.profile ?? "?" })
            : t("admin.itsmImport.znunyMissing")}
        </p>
      </div>
      {counts && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatTile testId="itsm-import-count-cis" label={t("admin.itsmImport.counts.cis")} value={num(counts.cis)} />
          <StatTile testId="itsm-import-count-classes" label={t("admin.itsmImport.counts.classes")} value={num(counts.classes)} />
          <StatTile testId="itsm-import-count-changes" label={t("admin.itsmImport.counts.changes")} value={num(counts.changes)} />
          <StatTile testId="itsm-import-count-workorders" label={t("admin.itsmImport.counts.workorders")} value={num(counts.workorders)} />
        </div>
      )}
      <p className="text-sm text-muted" data-testid="itsm-import-last-run">
        {last
          ? t("admin.itsmImport.lastRun", {
              kind: t(`admin.itsmImport.kind.${last.kind}`),
              time: formatDateTime(last.started_at, locale),
              status: t(`admin.itsmImport.status.${last.status}`),
            })
          : t("admin.itsmImport.noRun")}
      </p>
    </section>
  );
}
