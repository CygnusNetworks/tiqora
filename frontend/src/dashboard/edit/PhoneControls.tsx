import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";

const BTN =
  "inline-flex min-h-11 items-center justify-center gap-1.5 rounded-[10px] border border-hairline bg-surface text-[13px] text-ink transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-surface";

/**
 * Phone edit controls of one widget (Mobile mockup): "↑ Nach oben", "↓ Nach unten" (the phone
 * order is the array order) and the settings button. 44 px touch targets.
 */
export function PhoneControls({
  widgetId,
  isFirst,
  isLast,
  onShift,
  onSettings,
}: {
  widgetId: string;
  isFirst: boolean;
  isLast: boolean;
  onShift: (dir: "up" | "down") => void;
  onSettings: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex gap-2" data-testid={`widget-${widgetId}-phone-controls`}>
      <button type="button" data-shift="up" disabled={isFirst} onClick={() => onShift("up")} className={cn(BTN, "flex-1 px-3")}>
        <span aria-hidden="true">↑</span>
        {t("dashboards.edit.up")}
      </button>
      <button type="button" data-shift="down" disabled={isLast} onClick={() => onShift("down")} className={cn(BTN, "flex-1 px-3")}>
        <span aria-hidden="true">↓</span>
        {t("dashboards.edit.down")}
      </button>
      <button
        type="button"
        aria-label={t("dashboards.edit.settings")}
        title={t("dashboards.edit.settings")}
        onClick={onSettings}
        className={cn(BTN, "w-11 shrink-0")}
      >
        <GearIcon />
      </button>
    </div>
  );
}

export function GearIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
