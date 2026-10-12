import { useId, useRef, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { LockIcon } from "@/components/ui/icons";
import type { DashboardTab } from "@/lib/dashboardsApi";
import { cn } from "@/lib/cn";
import { TabMenu } from "./TabMenu";

const FOCUS = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/**
 * The tab row of the home page (WAI-ARIA tabs, manual activation): arrow keys / Home / End move the
 * focus, Enter or Space opens the dashboard. A locked role template carries a lock and the
 * description "Verbindlich"; a dashboard shared with the agent names its owner. After the tab list
 * (outside `role="tablist"`, so the list owns only tabs and stays one tab stop) sit the "..." menu of
 * the SELECTED tab (rename, move, copy, delete) and the "+" button.
 */
export interface TabActions {
  onRename: (tab: DashboardTab) => void;
  onMove: (tab: DashboardTab, dir: -1 | 1) => void;
  onDelete: (tab: DashboardTab) => void;
  onCopy: (tab: DashboardTab) => void;
  /** "Als Wallboard anzeigen"; without it the menu has no such item. */
  onWallboard?: (tab: DashboardTab) => void;
  /** "Wallboard-Link erstellen …" (admins); without it the menu has no such item. */
  onWallboardLink?: (tab: DashboardTab) => void;
}

export function DashboardTabs({
  tabs,
  selectedId,
  onSelect,
  onCreate,
  actions,
  panelId,
}: {
  tabs: DashboardTab[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onCreate: () => void;
  /** The menu of the selected tab; without it there is no menu button. */
  actions?: TabActions;
  /** Id of the tab panel (the page's content area); tab ids are `<panelId>-tab-<id>`. */
  panelId?: string;
}) {
  const { t } = useTranslation();
  const baseId = useId();
  const refs = useRef(new Map<number, HTMLButtonElement>());
  const ownIds = tabs.filter((tab) => tab.is_owner).map((tab) => tab.id);
  const selectedTab = tabs.find((tab) => tab.id === selectedId) ?? null;
  const focusable = selectedTab ? selectedTab.id : (tabs[0]?.id ?? null);

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = tabs.length - 1;
    const rtl = getComputedStyle(e.currentTarget).direction === "rtl";
    const next = rtl ? "ArrowLeft" : "ArrowRight";
    const prev = rtl ? "ArrowRight" : "ArrowLeft";
    let to: number | null = null;
    if (e.key === next) to = index === last ? 0 : index + 1;
    else if (e.key === prev) to = index === 0 ? last : index - 1;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = last;
    if (to === null) return;
    e.preventDefault();
    refs.current.get(tabs[to].id)?.focus();
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <div role="tablist" aria-label={t("dashboards.tabs.label")} className="flex flex-wrap gap-1.5">
        {tabs.map((tab, index) => {
          const selected = tab.id === selectedId;
          const locked = tab.kind === "template" && tab.locked;
          const owner = !tab.is_owner && tab.kind === "shared" && tab.owner_name ? tab.owner_name : null;
          const descId = `${baseId}-d${tab.id}`;
          const description = locked ? t("dashboards.tabs.locked") : owner ? t("dashboards.tabs.owner", { name: owner }) : null;
          return (
            <button
              key={tab.id}
              ref={(el) => {
                if (el) refs.current.set(tab.id, el);
                else refs.current.delete(tab.id);
              }}
              type="button"
              role="tab"
              id={panelId ? `${panelId}-tab-${tab.id}` : `${baseId}-t${tab.id}`}
              aria-selected={selected}
              aria-controls={panelId}
              aria-describedby={description ? descId : undefined}
              tabIndex={tab.id === focusable ? 0 : -1}
              data-testid={`dashboard-tab-${tab.id}`}
              onClick={() => onSelect(tab.id)}
              onKeyDown={(e) => onKeyDown(e, index)}
              className={cn(
                "inline-flex max-w-[16rem] items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] transition-colors",
                FOCUS,
                selected
                  ? "border-accent bg-accent/10 font-semibold text-ink"
                  : "border-hairline text-muted hover:bg-surface-subtle hover:text-ink",
              )}
            >
              {locked && <LockIcon className="h-3.5 w-3.5 shrink-0" />}
              <span className="truncate">{tab.name}</span>
              {owner && (
                <span aria-hidden="true" className="truncate text-[12px] font-normal text-muted">
                  · {owner}
                </span>
              )}
              {description && (
                <span id={descId} hidden>
                  {description}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {actions && selectedTab && (
        <TabMenu
          tab={selectedTab}
          index={ownIds.indexOf(selectedTab.id)}
          count={ownIds.length}
          onRename={() => actions.onRename(selectedTab)}
          onMove={(dir) => actions.onMove(selectedTab, dir)}
          onDelete={() => actions.onDelete(selectedTab)}
          onCopy={() => actions.onCopy(selectedTab)}
          onWallboard={actions.onWallboard ? () => actions.onWallboard?.(selectedTab) : undefined}
          onWallboardLink={actions.onWallboardLink ? () => actions.onWallboardLink?.(selectedTab) : undefined}
        />
      )}
      <button
        type="button"
        aria-label={t("dashboards.tabs.new")}
        title={t("dashboards.tabs.new")}
        onClick={onCreate}
        className={cn(
          "rounded-full border border-dashed border-hairline px-[11px] py-1.5 text-[13px] leading-none text-muted transition-colors hover:bg-surface-subtle hover:text-ink",
          FOCUS,
        )}
      >
        +
      </button>
    </div>
  );
}
