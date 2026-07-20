import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { SearchIcon } from "@/components/ui/icons";

/**
 * Top-bar search launcher. Renders a compact "Search ⌘K" trigger that opens a
 * dialog with a single input; submitting navigates to the existing agent
 * search route (`/agent/search?q=…`). Binds ⌘K / Ctrl-K globally to open it.
 * The legacy "/" shortcut (focus the sidebar search field) stays in
 * AgentShell — this adds the command palette without replacing it.
 */
export function CommandSearch() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      // Focus after the dialog has mounted its input.
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      setQ("");
    }
  }, [open]);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    setOpen(false);
    void navigate({ to: "/agent/search", search: { q: term } });
  };

  return (
    <>
      <button
        type="button"
        data-testid="command-search-trigger"
        onClick={() => setOpen(true)}
        className="flex h-8 items-center gap-2 rounded-lg border border-hairline bg-surface-subtle pl-2.5 pr-2 text-muted transition-colors duration-100 hover:border-accent/40 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      >
        <SearchIcon className="text-[15px]" />
        <span className="hidden text-[12.5px] sm:inline">{t("search.title")}</span>
        <kbd className="hidden shrink-0 rounded border border-hairline bg-surface px-1.5 py-0.5 font-mono text-[10px] font-medium sm:inline">
          ⌘K
        </kbd>
      </button>

      <Dialog open={open} onClose={() => setOpen(false)} title={t("search.title")}>
        <form onSubmit={onSubmit} data-testid="command-search-form">
          <div className="flex items-center gap-2 rounded-lg border border-hairline bg-surface-subtle px-3 py-2 focus-within:border-accent">
            <SearchIcon className="text-[17px] text-muted" />
            <input
              ref={inputRef}
              data-testid="command-search-input"
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("search.commandHint")}
              className="w-full min-w-0 bg-transparent text-[13.5px] text-ink placeholder:text-muted focus:outline-none"
            />
          </div>
          <p className="mt-2 text-[11.5px] text-muted">{t("search.hint")}</p>
        </form>
      </Dialog>
    </>
  );
}
