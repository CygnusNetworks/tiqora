import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { useTheme } from "@/themes/theme";
import { Button } from "@/components/ui/Button";
import { ShortcutHelp } from "@/components/agent/ShortcutHelp";

export function AgentShell({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "?") {
        e.preventDefault();
        setHelpOpen(true);
      }
      if (e.key === "/") {
        e.preventDefault();
        document.getElementById("agent-search")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    void navigate({ to: "/agent/search", search: { q: term } });
  };

  const switchLang = () => {
    const next = i18n.language?.startsWith("de") ? "en" : "de";
    void i18n.changeLanguage(next);
    localStorage.setItem("tiqora-lang", next);
  };

  return (
    <div className="flex min-h-screen flex-col">
      <div className="bg-warn/15 text-warn border-b border-border px-4 py-1.5 text-center text-xs font-medium">
        ⚠️ {t("app.devBanner")}
      </div>
      <header className="sticky top-0 z-20 border-b border-border bg-surface-elevated">
        <div className="flex items-center gap-3 px-3 py-2">
          <Link to="/agent" className="shrink-0 text-base font-semibold text-accent">
            {t("app.name")}
          </Link>
          <nav className="hidden items-center gap-2 text-sm sm:flex">
            <Link
              to="/agent"
              className="rounded px-2 py-1 text-muted hover:bg-surface hover:text-ink"
            >
              {t("nav.dashboard")}
            </Link>
            <Link
              to="/agent/queues"
              className="rounded px-2 py-1 text-muted hover:bg-surface hover:text-ink"
            >
              {t("nav.queues")}
            </Link>
          </nav>
          <form onSubmit={onSearch} className="mx-auto flex max-w-md flex-1">
            <input
              id="agent-search"
              data-testid="header-search"
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("search.placeholder")}
              className="w-full rounded-md border border-border bg-surface px-3 py-1.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </form>
          <div className="flex shrink-0 items-center gap-1.5 text-sm">
            <Button variant="ghost" size="sm" onClick={() => setHelpOpen(true)} title="?">
              ?
            </Button>
            <Button variant="ghost" size="sm" onClick={toggleTheme}>
              {theme === "dark" ? "☀" : "☾"}
            </Button>
            <Button variant="ghost" size="sm" onClick={switchLang}>
              {i18n.language?.startsWith("de") ? "DE" : "EN"}
            </Button>
            {user && (
              <span
                className="hidden max-w-[10rem] truncate text-xs text-muted md:inline"
                data-testid="current-user"
                title={user.login}
              >
                {user.first_name || user.login} {user.last_name}
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              data-testid="logout-btn"
              onClick={() => {
                void logout().then(() => navigate({ to: "/login" }));
              }}
            >
              {t("auth.logout")}
            </Button>
          </div>
        </div>
      </header>
      <main className="flex flex-1 flex-col">{children}</main>
      <ShortcutHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
