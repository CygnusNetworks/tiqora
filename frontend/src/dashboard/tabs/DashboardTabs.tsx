import { useId, useRef, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { LockIcon } from "@/components/ui/icons";
import type { DashboardTab } from "@/lib/dashboardsApi";
import { cn } from "@/lib/cn";

const FOCUS = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/**
 * The tab row of the home page (WAI-ARIA tabs, manual activation): arrow keys / Home / End move the
 * focus, Enter or Space opens the dashboard. A locked role template carries a lock and the
 * description "Verbindlich"; a dashboard shared with the agent names its owner. The "+" button sits
 * after the tab list; without `onCreate` it is shown but disabled.
 */
export function DashboardTabs({
  tabs,
  selectedId,
  onSelect,
  onCreate,
}: {
  tabs: DashboardTab[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onCreate?: () => void;
}) {
  const { t } = useTranslation();
  const baseId = useId();
  const refs = useRef(new Map<number, HTMLButtonElement>());
  const focusable = tabs.some((tab) => tab.id === selectedId) ? selectedId : (tabs[0]?.id ?? null);

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
              id={`${baseId}-t${tab.id}`}
              aria-selected={selected}
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
      <button
        type="button"
        aria-label={t("dashboards.tabs.new")}
        title={t("dashboards.tabs.new")}
        disabled={!onCreate}
        onClick={onCreate}
        className={cn(
          "rounded-full border border-dashed border-hairline px-[11px] py-1.5 text-[13px] leading-none text-muted transition-colors hover:bg-surface-subtle hover:text-ink disabled:cursor-default disabled:hover:bg-transparent",
          FOCUS,
        )}
      >
        +
      </button>
    </div>
  );
}
