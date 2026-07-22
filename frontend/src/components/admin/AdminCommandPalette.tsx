import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { cn } from "@/lib/cn";
import { ADMIN_PAGES, rankAdminPages } from "@/lib/adminSearch";

/**
 * ⌘K / Ctrl+K command palette for the admin area. Filters the page registry
 * (name > keyword > description ranking, see rankAdminPages) and navigates
 * on Enter/click. Opened from AdminShell's global shortcut and its sidebar
 * trigger, and from the dashboard's own search field.
 */
export function AdminCommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
    }
  }, [open]);

  const results = useMemo(() => rankAdminPages(ADMIN_PAGES, query, t), [query, t]);

  useEffect(() => {
    setActiveIndex(0);
  }, [results.length]);

  const go = (route: string) => {
    onClose();
    void navigate({ to: route });
  };

  return (
    <Dialog open={open} onClose={onClose} title={t("admin.commandPalette.title")} className="max-w-lg">
      <input
        autoFocus
        data-testid="admin-search-input"
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={t("admin.commandPalette.placeholder")}
        className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActiveIndex((i) => Math.min(i + 1, results.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActiveIndex((i) => Math.max(i - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            const hit = results[activeIndex];
            if (hit) go(hit.route);
          }
        }}
      />
      <ul className="mt-3 max-h-80 list-none space-y-0.5 overflow-y-auto" data-testid="admin-search-results">
        {results.length === 0 ? (
          <li className="px-2.5 py-3 text-sm text-muted">{t("admin.commandPalette.noResults")}</li>
        ) : (
          results.map((page, index) => (
            <li key={page.slug}>
              <button
                type="button"
                data-testid={`admin-search-result-${page.slug}`}
                onClick={() => go(page.route)}
                onMouseEnter={() => setActiveIndex(index)}
                className={cn(
                  "block w-full rounded-md px-2.5 py-2 text-left transition-colors duration-100",
                  index === activeIndex
                    ? "bg-accent-dim text-accent"
                    : "text-ink hover:bg-surface-subtle",
                )}
              >
                <div className="text-sm font-medium">{t(page.nameKey)}</div>
                <div className="text-xs text-muted">{t(page.descriptionKey)}</div>
              </button>
            </li>
          ))
        )}
      </ul>
    </Dialog>
  );
}
