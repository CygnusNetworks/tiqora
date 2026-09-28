import { useTranslation } from "react-i18next";
import type { TelegramButtonIn } from "@/lib/api";
import { MAX_BUTTON_LABEL, MAX_BUTTONS } from "./chatComposerHelpers";

/**
 * Reply buttons under the outgoing message. The preset wires "Ja"/"Nein" to
 * the resolve actions (the backend closes or reopens on the tap); free
 * labels come back as a plain customer reply.
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
          className="inline-flex items-center gap-0.5 rounded-full border border-hairline bg-surface py-0.5 pl-2 pr-0.5"
        >
          <input
            value={b.label}
            maxLength={MAX_BUTTON_LABEL}
            onChange={(e) => setLabel(i, e.target.value)}
            aria-label={t("ticket.telegram.composer.buttonLabel", { n: i + 1 })}
            placeholder={t("ticket.telegram.composer.buttonLabelPlaceholder")}
            data-testid={`tg-buttons-label-${i}`}
            className="w-24 border-0 bg-transparent p-0 text-xs text-ink placeholder:text-muted focus:outline-none"
          />
          <button
            type="button"
            aria-label={t("ticket.telegram.composer.buttonRemove")}
            data-testid={`tg-buttons-remove-${i}`}
            onClick={() => onChange(buttons.filter((_, j) => j !== i))}
            className="rounded-full px-1 text-xs text-muted hover:text-danger"
          >
            ×
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
    </div>
  );
}
