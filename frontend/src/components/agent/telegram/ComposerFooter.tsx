import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { MAX_MESSAGE_LENGTH } from "./chatComposerHelpers";

const kbdCls =
  "rounded border border-b-2 border-hairline bg-surface-subtle px-1 font-mono text-[10.5px] text-ink";

/**
 * Shortcut hints, the snippet button and the length counter. The snippet
 * button opens the same picker as typing `/`, so the snippets can be found
 * without knowing the shortcut; its count says up front whether the queue
 * has any.
 */
export function ComposerFooter({
  length,
  snippetCount,
  onSnippets,
}: {
  length: number;
  /** `null` while the templates are still loading. */
  snippetCount: number | null;
  onSnippets: () => void;
}) {
  const { t } = useTranslation();
  const tooLong = length > MAX_MESSAGE_LENGTH;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 text-[11.5px] text-muted">
      <span className="inline-flex flex-wrap items-center gap-1">
        <kbd className={kbdCls}>Enter</kbd> {t("ticket.telegram.composer.hintSend")} ·{" "}
        <kbd className={kbdCls}>⇧ Enter</kbd> {t("ticket.telegram.composer.hintNewline")} ·{" "}
        <button
          type="button"
          data-testid="tg-composer-snippets"
          // Keep the textarea focused so the snippet lands at the caret.
          onMouseDown={(e) => e.preventDefault()}
          onClick={onSnippets}
          className="inline-flex items-center gap-1 rounded-md border border-hairline bg-surface-subtle px-1.5 py-px hover:text-ink"
        >
          <kbd className={kbdCls}>/</kbd> {t("ticket.telegram.composer.snippets")}
          {snippetCount !== null && (
            <span className="font-mono tabular-nums text-accent">{snippetCount}</span>
          )}
        </button>
      </span>
      <span
        data-testid="tg-composer-counter"
        className={cn("font-mono tabular-nums", tooLong && "font-semibold text-danger")}
      >
        {length} / {MAX_MESSAGE_LENGTH}
      </span>
    </div>
  );
}
