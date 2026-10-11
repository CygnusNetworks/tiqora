import { useTranslation } from "react-i18next";
import type { WidgetMode } from "@/plugins/dashboardWidgets";
import { linkTabIndex } from "../chrome/mode";
import { useWidgetChrome } from "../chrome/WidgetFrame";

/**
 * "Suche wählen": the way out of a tile whose saved search is gone or no longer shared. Only for
 * people who can edit the dashboard (the drawer opens from there); everyone else just reads the message.
 */
export function ChooseSourceAction({ mode }: { mode: WidgetMode }) {
  const { t } = useTranslation();
  const chrome = useWidgetChrome();
  if (!chrome.canEdit || !chrome.onConfigure) return null;
  return (
    <button
      type="button"
      onClick={chrome.onConfigure}
      tabIndex={linkTabIndex(mode)}
      className="text-sm font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
    >
      {t("dashboards.widgets.chooseSearch")}
    </button>
  );
}
