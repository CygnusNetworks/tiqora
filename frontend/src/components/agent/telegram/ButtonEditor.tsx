import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { TelegramButtonIn } from "@/lib/api";
import { MAX_BUTTON_LABEL, MAX_BUTTONS } from "./chatComposerHelpers";

/**
 * Reply buttons under the outgoing message. The preset wires "Ja"/"Nein" to
 * the resolve actions (the backend closes or reopens on the tap); free
 * labels come back as a plain customer reply. Each button has its own
 * remove control (and Backspace in an empty label removes it); "Alle
 * entfernen" clears the lot.
 */
export function ButtonEditor({
  buttons,
  onChange,
  onPreset,
}: {
  buttons: TelegramButtonIn[];
  onChange: (buttons: TelegramButtonIn[]) => void;
  onPreset: () => void;
}) {
  const { t } = useTranslation();
  const setLabel = (i: number, label: string) =>
    onChange(buttons.map((b, j) => (j === i ? { ...b, label } : b)));
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  // After a removal, focus lands on the previous label (or the next one when
  // the first was removed) so Backspace can keep deleting.
  const focusAfterRemove = useRef<number | null>(null);
  useEffect(() => {
    const i = focusAfterRemove.current;
    if (i === null) return;
    focusAfterRemove.current = null;
    inputs.current[Math.min(i, buttons.length - 1)]?.focus();
  }, [buttons.length]);
  const remove = (i: number, refocus = false) => {
    if (refocus) focusAfterRemove.current = Math.max(i - 1, 0);
    onChange(buttons.filter((_, j) => j !== i));
  };

  return (
    <div
      className="flex flex-wrap items-center gap-1.5 rounded-lg border border-hairline bg-surface-subtle/60 px-2 py-1.5"
      data-testid="tg-button-editor"
    >
      <button
        type="button"
        data-testid="tg-buttons-preset"
        onClick={onPreset}
        className="rounded-full border border-accent/50 bg-accent-dim px-2 py-0.5 text-xs text-accent hover:bg-accent/20"
      >
        {t("ticket.telegram.composer.buttonsPreset")}
      </button>
      {buttons.map((b, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 rounded-full border border-hairline bg-surface py-0.5 pl-2 pr-0.5"
        >
          <input
            ref={(el) => {
              inputs.current[i] = el;
            }}
            value={b.label}
            maxLength={MAX_BUTTON_LABEL}
            onChange={(e) => setLabel(i, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Backspace" && b.label === "") {
                e.preventDefault();
                remove(i, true);
              }
            }}
            aria-label={t("ticket.telegram.composer.buttonLabel", { n: i + 1 })}
            placeholder={t("ticket.telegram.composer.buttonLabelPlaceholder")}
            data-testid={`tg-buttons-label-${i}`}
            className="w-24 border-0 bg-transparent p-0 text-xs text-ink placeholder:text-muted focus:outline-none"
          />
          <button
            type="button"
            aria-label={t("ticket.telegram.composer.buttonRemove")}
            title={t("ticket.telegram.composer.buttonRemove")}
            data-testid={`tg-buttons-remove-${i}`}
            onClick={() => remove(i)}
            className="grid h-5 w-5 shrink-0 place-items-center rounded-full border border-hairline bg-surface-subtle text-sm leading-none text-ink hover:border-danger/60 hover:bg-danger/10 hover:text-danger focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-danger"
          >
            <span aria-hidden>×</span>
          </button>
        </span>
      ))}
      {buttons.length < MAX_BUTTONS && (
        <button
          type="button"
          data-testid="tg-buttons-add"
          onClick={() => onChange([...buttons, { label: "", action: "reply" }])}
          className="rounded-full px-2 py-0.5 text-xs text-muted hover:text-ink"
        >
          + {t("ticket.telegram.composer.buttonAdd")}
        </button>
      )}
      {buttons.length > 0 && (
        <button
          type="button"
          data-testid="tg-buttons-clear"
          onClick={() => onChange([])}
          className="ml-auto rounded-full border border-hairline px-2 py-0.5 text-xs text-muted hover:border-danger/60 hover:text-danger"
        >
          {t("ticket.telegram.composer.buttonsClear")}
        </button>
      )}
    </div>
  );
}
