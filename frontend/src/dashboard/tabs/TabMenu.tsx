import { useTranslation } from "react-i18next";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import type { DashboardTab } from "@/lib/dashboardsApi";
import { cn } from "@/lib/cn";

const FOCUS = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/**
 * The "..." menu of one tab. Own dashboards (personal, or shared by the agent) can be renamed,
 * moved and deleted; a dashboard of someone else or a role template can only be copied.
 * `index` / `count` position the tab among the agent's OWN dashboards (the ones `order` permutes);
 * the last own dashboard cannot be deleted.
 */
export function TabMenu({
  tab,
  index,
  count,
  onRename,
  onMove,
  onDelete,
  onCopy,
}: {
  tab: DashboardTab;
  index: number;
  count: number;
  onRename: () => void;
  onMove: (dir: -1 | 1) => void;
  onDelete: () => void;
  onCopy: () => void;
}) {
  const { t } = useTranslation();
  const own = tab.is_owner;
  return (
    <Menu
      align="left"
      maxHeight={null}
      trigger={({ open, ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          aria-label={t("dashboards.manage.menu", { name: tab.name })}
          data-testid={`dashboard-tab-menu-${tab.id}`}
          {...toggleProps}
          className={cn(
            "flex h-7 w-6 items-center justify-center rounded-full text-[15px] leading-none text-muted transition-colors hover:bg-surface-subtle hover:text-ink",
            open && "bg-surface-subtle text-ink",
            FOCUS,
          )}
        >
          <span aria-hidden="true">⋯</span>
        </button>
      )}
    >
      {own ? (
        <>
          <MenuItem onSelect={onRename}>{t("dashboards.manage.rename")}</MenuItem>
          <MenuItem onSelect={() => onMove(-1)} disabled={index <= 0}>
            {t("dashboards.manage.moveLeft")}
          </MenuItem>
          <MenuItem onSelect={() => onMove(1)} disabled={index >= count - 1}>
            {t("dashboards.manage.moveRight")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem
            danger
            onSelect={onDelete}
            softDisabled={count <= 1}
            title={count <= 1 ? t("dashboards.manage.lastKept") : undefined}
          >
            {t("dashboards.manage.delete")}
          </MenuItem>
        </>
      ) : (
        <MenuItem onSelect={onCopy}>{t("dashboards.manage.copy")}</MenuItem>
      )}
    </Menu>
  );
}
