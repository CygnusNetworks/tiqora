import { useTranslation } from "react-i18next";
import type { CmdbStatus } from "@/lib/cmdbApi";

/** D13 mode notice: in Mode A the CMDB is still kept in Znuny. Renders nothing in Tiqora mode. */
export function CmdbModeNotice({ status }: { status: Pick<CmdbStatus, "mode"> }) {
  const { t } = useTranslation();
  if (status.mode === "tiqora") return null;
  return (
    <div
      role="note"
      data-testid="cmdb-mode-notice"
      className="rounded-md border border-hairline bg-surface-subtle px-4 py-3 text-sm text-ink"
    >
      {status.mode === "znuny_readthrough" ? t("cmdb.modeNotice.znuny") : t("cmdb.modeNotice.off")}
    </div>
  );
}
