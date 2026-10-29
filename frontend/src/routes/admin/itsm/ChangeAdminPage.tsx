import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Tabs } from "@/components/ui/Tabs";
import { ChangeArchiveTab } from "./ChangeArchiveTab";
import { ChangeCabTab } from "./ChangeCabTab";
import { ChangeFreezeTab } from "./ChangeFreezeTab";
import { ChangeRiskQuestionsTab } from "./ChangeRiskQuestionsTab";
import { ChangeTemplatesTab } from "./ChangeTemplatesTab";
import { CHANGE_ADMIN_TABS, parseChangeAdminTab } from "./changeAdminTabs";

/**
 * `/admin/itsm/change`: change management administration in five tabs (templates, CAB, risk
 * questions, freeze windows, Znuny change archive) and the link to the settings section `itsm_change`.
 */
export function ChangeAdminPage() {
  const { t } = useTranslation();
  const search = useSearch({ strict: false }) as { tab?: unknown };
  const navigate = useNavigate();
  const tab = parseChangeAdminTab(search.tab);
  const setTab = (next: string) => {
    const value = parseChangeAdminTab(next);
    void navigate({ to: "/admin/itsm/change", search: { tab: value === "templates" ? undefined : value }, replace: true });
  };

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-change-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.itsmChange.title")}</h1>
        <Link
          to="/admin/settings/$section"
          params={{ section: "itsm_change" }}
          className="text-sm text-accent hover:underline"
          data-testid="change-admin-settings"
        >
          {t("admin.itsmChange.settingsLink")}
        </Link>
      </div>
      <Tabs
        items={CHANGE_ADMIN_TABS.map((id) => ({ id, label: t(`admin.itsmChange.tabs.${id}`) }))}
        value={tab}
        onChange={setTab}
        ariaLabel={t("admin.itsmChange.tabsLabel")}
      />
      {tab === "templates" && <ChangeTemplatesTab />}
      {tab === "cab" && <ChangeCabTab />}
      {tab === "risk" && <ChangeRiskQuestionsTab />}
      {tab === "freeze" && <ChangeFreezeTab />}
      {tab === "archive" && <ChangeArchiveTab />}
    </div>
  );
}
