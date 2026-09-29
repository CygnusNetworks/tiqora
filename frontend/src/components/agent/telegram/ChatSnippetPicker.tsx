import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useOptionalAuth } from "@/auth/AuthContext";
import type { TemplateOut } from "@/lib/api";
import { cn } from "@/lib/cn";
import { snippetText } from "./chatComposerHelpers";

const panelCls =
  "absolute bottom-full left-0 right-0 z-20 mb-1 max-h-56 overflow-y-auto rounded-lg border border-hairline bg-surface-elevated p-1 shadow-lg";

/**
 * The list half of the `/` snippet picker. Focus stays in the composer's
 * textarea (the agent keeps typing the filter), so the keyboard handling
 * lives there and this only renders the matches and takes clicks. The
 * composer mounts it without matches only when the agent opened it on
 * purpose (the "Textbausteine" button) and the queue has no chat snippets —
 * then it says so instead of staying silent; "/etc/hosts" typed into a
 * message still never shows an empty popup.
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
  const user = useOptionalAuth()?.user;
  if (matches.length === 0) {
    return (
      <div className={cn(panelCls, "grid gap-1 p-3 text-xs")} data-testid="tg-snippet-empty">
        <strong className="text-sm text-ink">{t("ticket.telegram.composer.snippetsEmptyTitle")}</strong>
        <span className="text-muted">{t("ticket.telegram.composer.snippetsEmptyBody")}</span>
        {user?.is_admin && (
          <Link to="/admin/templates" className="justify-self-start font-semibold text-accent hover:underline">
            {t("ticket.telegram.composer.snippetsEmptyLink")} →
          </Link>
        )}
      </div>
    );
  }
  return (
    <div role="listbox" aria-label={t("ticket.telegram.composer.snippets")} data-testid="tg-snippet-picker" className={panelCls}>
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
