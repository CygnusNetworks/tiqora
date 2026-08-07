import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import {
  displayToMinutes,
  loadTimeUnitMode,
  minutesToDisplay,
  saveTimeUnitMode,
  type TimeUnitMode,
} from "@/lib/timeUnits";
import { Popover } from "@/components/ui/Popover";
import { usePopoverClose } from "@/components/ui/popoverContext";
import { TimePresetButtons, TimeUnitToggle } from "./TimeUnitControls";

/**
 * Minute/hour field for a composer footer, sized to sit next to the send
 * button rather than in a panel of its own. Empty means "book nothing" and
 * the chip stays quiet; a value tints it accent so it is obvious that
 * sending will also book. Sending is still called "Senden" — booking is a
 * side effect of the field being filled, not a second decision at the button.
 *
 * `value`/`onChange` stay in whole minutes (Znuny's `time_unit`, same
 * contract `ReplyDialog`/`postComposerExtras` already expect) — the
 * minutes/hours toggle is purely local display state, converted at the edges.
 */
export function ComposerTimeChip({
  value,
  onChange,
  testId = "composer-time",
}: {
  value: string;
  onChange: (value: string) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<TimeUnitMode>(() => loadTimeUnitMode());
  const [text, setText] = useState(() => minutesToDisplay(Number(value) || 0, mode));

  useEffect(() => saveTimeUnitMode(mode), [mode]);

  // Parent resets `value` to "" after a successful send — mirror that locally.
  useEffect(() => {
    if (value === "") setText("");
  }, [value]);

  const filled = value.trim() !== "" && Number(value) > 0;

  const handleTextChange = (nextText: string) => {
    setText(nextText);
    const minutes = displayToMinutes(nextText, mode);
    onChange(minutes === null ? "" : String(minutes));
  };

  const handleModeChange = (nextMode: TimeUnitMode) => {
    const minutes = displayToMinutes(text, mode);
    setMode(nextMode);
    setText(minutes === null ? "" : minutesToDisplay(minutes, nextMode));
  };

  return (
    <div
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] transition-colors duration-100",
        filled
          ? "border-accent/40 bg-accent-dim text-accent"
          : "border-hairline text-muted hover:text-ink",
      )}
      title={t("ticket.timeChipHint")}
    >
      <span aria-hidden>⏱</span>
      <TimeUnitToggle mode={mode} onChange={handleModeChange} size="sm" testId={`${testId}-mode`} />
      <input
        type="number"
        min="0"
        step={mode === "min" ? 1 : 0.25}
        inputMode="decimal"
        value={text}
        onChange={(e) => handleTextChange(e.target.value)}
        placeholder="0"
        aria-label={t("ticket.timeUnits")}
        data-testid={testId}
        className="w-14 rounded border border-hairline bg-surface px-1 py-0.5 text-right font-mono text-[11px] tabular-nums text-current placeholder:text-muted focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      />
      <span aria-hidden>
        {mode === "min" ? t("ticket.timeUnitAbbrev") : t("ticket.timeUnitHoursAbbrev")}
      </span>
      <Popover
        align="right"
        label={t("ticket.timePresets")}
        panelTestId={`${testId}-presets-panel`}
        trigger={({ ref, toggleProps }) => (
          <button
            ref={ref}
            type="button"
            aria-label={t("ticket.timePresets")}
            data-testid={`${testId}-presets-trigger`}
            {...toggleProps}
            className="px-0.5 text-muted hover:text-ink"
          >
            ⌄
          </button>
        )}
      >
        <PresetPanel
          testId={`${testId}-preset`}
          onPick={(minutes) => handleTextChange(minutesToDisplay(minutes, mode))}
        />
      </Popover>
    </div>
  );
}

function PresetPanel({
  onPick,
  testId,
}: {
  onPick: (minutes: number) => void;
  testId?: string;
}) {
  const closePopover = usePopoverClose();
  return (
    <TimePresetButtons
      testId={testId}
      onPick={(minutes) => {
        onPick(minutes);
        closePopover();
      }}
    />
  );
}
