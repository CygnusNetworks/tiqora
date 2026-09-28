import { useTranslation } from "react-i18next";
import type { TemplateOut } from "@/lib/api";
import { cn } from "@/lib/cn";
import { snippetText } from "./chatComposerHelpers";

/**
 * The list half of the `/` snippet picker. Focus stays in the composer's
 * textarea (the agent keeps typing the filter), so the keyboard handling
 * lives there and this only renders the matches and takes clicks. The
 * composer doesn't mount it without matches, so "/etc/hosts" in a message
 * never shows an empty popup.
 */
export function ChatSnippetPicker({
  matches,
  activeIndex,
  onPick,
}: {
  matches: TemplateOut[];
  activeIndex: number;
  onPick: (tpl: TemplateOut) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="listbox"
      aria-label={t("ticket.telegram.composer.snippets")}
      data-testid="tg-snippet-picker"
      className="absolute bottom-full left-0 right-0 z-20 mb-1 max-h-56 overflow-y-auto rounded-lg border border-hairline bg-surface-elevated p-1 shadow-lg"
    >
      {matches.map((tpl, i) => (
        <button
          key={tpl.id}
          type="button"
          role="option"
          aria-selected={i === activeIndex}
          data-testid={`tg-snippet-${tpl.id}`}
          // Keep the textarea focused so the caret position survives the click.
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onPick(tpl)}
          className={cn(
            "flex w-full min-w-0 items-baseline gap-2 rounded-md px-2 py-1.5 text-left text-xs",
            i === activeIndex ? "bg-accent-dim text-ink" : "text-ink hover:bg-surface-subtle",
          )}
        >
          <span className="shrink-0 font-semibold">/{tpl.name}</span>
          <span className="min-w-0 truncate text-muted">{snippetText(tpl)}</span>
        </button>
      ))}
    </div>
  );
}
