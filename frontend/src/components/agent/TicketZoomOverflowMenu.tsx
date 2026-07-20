import { useTranslation } from "react-i18next";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/components/ui/Menu";

export type TicketZoomOverflowMenuProps = {
  tab: "articles" | "history";
  onTabChange: (tab: "articles" | "history") => void;
  /** Label for the current sort direction (already localised). */
  sortLabel: string;
  onToggleSort: () => void;
  onInternalNote: () => void;
  /** Whether "Start process" is available (ticket not already in a process). */
  canStartProcess: boolean;
  onStartProcess: () => void;
};

/**
 * Compact ⋮ overflow for ticket-zoom secondary actions: Artikel/Historie,
 * Prozess starten, Interne Notiz, and article/history sort order.
 */
export function TicketZoomOverflowMenu({
  tab,
  onTabChange,
  sortLabel,
  onToggleSort,
  onInternalNote,
  canStartProcess,
  onStartProcess,
}: TicketZoomOverflowMenuProps) {
  const { t } = useTranslation();

  return (
    <Menu
      align="right"
      panelTestId="ticket-zoom-overflow-menu"
      trigger={({ ref, toggleProps, open }) => (
        <button
          ref={ref}
          type="button"
          data-testid="ticket-zoom-overflow-trigger"
          aria-label={t("ticket.overflow.menu")}
          title={t("ticket.overflow.menu")}
          className={`inline-flex h-8 w-8 items-center justify-center rounded-md border border-hairline text-muted transition-colors duration-100 hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent ${
            open ? "bg-surface-subtle text-ink" : "bg-surface"
          }`}
          {...toggleProps}
        >
          <OverflowIcon />
        </button>
      )}
    >
      <MenuLabel>{t("ticket.overflow.menu")}</MenuLabel>
      <MenuItem
        testId="overflow-tab-articles"
        selected={tab === "articles"}
        onSelect={() => onTabChange("articles")}
      >
        {t("ticket.overflow.articles")}
      </MenuItem>
      <MenuItem
        testId="overflow-tab-history"
        selected={tab === "history"}
        onSelect={() => onTabChange("history")}
      >
        {t("ticket.overflow.history")}
      </MenuItem>
      <MenuSeparator />
      <MenuItem testId="overflow-sort" onSelect={onToggleSort}>
        {t("ticket.overflow.sort")}: {sortLabel}
      </MenuItem>
      <MenuSeparator />
      <MenuItem testId="overflow-internal-note" onSelect={onInternalNote}>
        {t("ticket.overflow.internalNote")}
      </MenuItem>
      {canStartProcess && (
        <MenuItem testId="overflow-start-process" onSelect={onStartProcess}>
          {t("ticket.overflow.startProcess")}
        </MenuItem>
      )}
    </Menu>
  );
}

function OverflowIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="currentColor"
      aria-hidden="true"
    >
      <circle cx="12" cy="5" r="1.75" />
      <circle cx="12" cy="12" r="1.75" />
      <circle cx="12" cy="19" r="1.75" />
    </svg>
  );
}
