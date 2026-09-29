import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { TelegramButtonIn } from "@/lib/api";
import { MAX_BUTTON_LABEL, MAX_BUTTONS } from "./chatComposerHelpers";

/**
 * Reply buttons under the outgoing message. The preset wires "Ja"/"Nein" to
 * the resolve actions (the backend closes or reopens on the tap); free
 * labels come back as a plain customer reply. Each button has its own
 * remove control (and Backspace in an empty label removes it); "Keine
 * Buttons" in the header discards them all and closes the editor. The preset
 * is a link, not a chip, so it can't be mistaken for a button that was
 * already added.
 */
export function ButtonEditor({
  buttons,
  onChange,
  onPreset,
  onClose,
}: {
  buttons: TelegramButtonIn[];
  onChange: (buttons: TelegramButtonIn[]) => void;
  onPreset: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const setLabel = (i: number, label: string) =>
    onChange(buttons.map((b, j) => (j === i ? { ...b, label } : b)));
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  // After a removal, focus lands on the previous label (or the next one when
  // the first was removed) so Backspace can keep deleting. After an add, on
  // the new (empty) label.
  const focusAfterChange = useRef<number | null>(null);
  useEffect(() => {
    const i = focusAfterChange.current;
    if (i === null) return;
    focusAfterChange.current = null;
    inputs.current[Math.min(i, buttons.length - 1)]?.focus();
  }, [buttons.length]);
  const remove = (i: number, refocus = false) => {
    if (refocus) focusAfterChange.current = Math.max(i - 1, 0);
    onChange(buttons.filter((_, j) => j !== i));
  };
  const hasResolve = buttons.some((b) => b.action !== "reply");

  return (
    <div
      className="grid gap-1.5 rounded-lg border border-hairline bg-surface-subtle/60 px-2 py-1.5"
      data-testid="tg-button-editor"
    >
      <div className="flex items-center gap-2 text-[11.5px] text-muted">
        <span className="font-semibold text-ink">{t("ticket.telegram.composer.buttonsTitle")}</span>
        <span className="font-mono tabular-nums">
          {buttons.length} / {MAX_BUTTONS}
        </span>
        <button
          type="button"
          data-testid="tg-buttons-close"
          title={t("ticket.telegram.composer.buttonsCloseTitle")}
          onClick={onClose}
          className="ml-auto rounded-md border border-hairline bg-surface px-2 py-0.5 text-xs text-muted hover:border-danger/60 hover:bg-danger/10 hover:text-danger"
        >
          × {t("ticket.telegram.composer.buttonsClose")}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {buttons.map((b, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 rounded-md border border-hairline bg-surface py-0.5 pl-2 pr-0.5"
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
            {b.action !== "reply" && (
              <span className="shrink-0 text-[10px] font-semibold text-green">
                {t(
                  b.action === "resolve_yes"
                    ? "ticket.telegram.composer.buttonActionResolveYes"
                    : "ticket.telegram.composer.buttonActionResolveNo",
                )}
              </span>
            )}
            <button
              type="button"
              aria-label={t("ticket.telegram.composer.buttonRemove")}
              title={t("ticket.telegram.composer.buttonRemove")}
              data-testid={`tg-buttons-remove-${i}`}
              onClick={() => remove(i)}
              className="grid h-5 w-5 shrink-0 place-items-center rounded text-sm leading-none text-muted hover:bg-danger/10 hover:text-danger focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-danger"
            >
              <span aria-hidden>×</span>
            </button>
          </span>
        ))}
        {buttons.length < MAX_BUTTONS && (
          <button
            type="button"
            data-testid="tg-buttons-add"
            onClick={() => {
              focusAfterChange.current = buttons.length;
              onChange([...buttons, { label: "", action: "reply" }]);
            }}
            className="rounded-md border border-dashed border-hairline px-2 py-0.5 text-xs text-muted hover:border-muted hover:text-ink"
          >
            + {t("ticket.telegram.composer.buttonAdd")}
          </button>
        )}
        {!hasResolve && (
          <button
            type="button"
            data-testid="tg-buttons-preset"
            onClick={onPreset}
            className="ml-auto px-1 text-xs text-accent hover:underline"
          >
            {t("ticket.telegram.composer.buttonsPresetInsert")}
          </button>
        )}
      </div>

      <p className="text-[11px] text-muted" data-testid="tg-buttons-hint">
        {hasResolve
          ? t("ticket.telegram.composer.buttonsHintResolve")
          : buttons.length > 0
            ? t("ticket.telegram.composer.buttonsHintFree")
            : t("ticket.telegram.composer.buttonsHintEmpty")}
      </p>
    </div>
  );
}
