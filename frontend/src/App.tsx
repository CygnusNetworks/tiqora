import { Link, Navigate, Route, Routes } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ThemeProvider, useTheme } from "./themes/theme";
import AgentPage from "./routes/AgentPage";
import PortalPage from "./routes/PortalPage";
import AdminPage from "./routes/AdminPage";
import HomePage from "./routes/HomePage";

function Shell() {
  const { t, i18n } = useTranslation();
  const { theme, toggleTheme } = useTheme();

  const switchLang = () => {
    const next = i18n.language?.startsWith("de") ? "en" : "de";
    void i18n.changeLanguage(next);
    localStorage.setItem("tiqora-lang", next);
  };

  return (
    <div className="min-h-screen flex flex-col">
      <div className="bg-warn/15 text-warn border-b border-border px-4 py-2 text-center text-sm font-medium">
        ⚠️ {t("app.devBanner")}
      </div>
      <header className="border-b border-border bg-surface-elevated">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-6">
            <Link to="/" className="text-lg font-semibold text-accent">
              {t("app.name")}
            </Link>
            <nav className="flex gap-4 text-sm">
              <Link className="text-muted hover:text-ink" to="/agent">
                {t("nav.agent")}
              </Link>
              <Link className="text-muted hover:text-ink" to="/portal">
                {t("nav.portal")}
              </Link>
              <Link className="text-muted hover:text-ink" to="/admin">
                {t("nav.admin")}
              </Link>
            </nav>
          </div>
          <div className="flex gap-2 text-sm">
            <button
              type="button"
              onClick={toggleTheme}
              className="rounded border border-border px-2 py-1 text-muted hover:text-ink"
            >
              {t("nav.theme")}: {theme}
            </button>
            <button
              type="button"
              onClick={switchLang}
              className="rounded border border-border px-2 py-1 text-muted hover:text-ink"
            >
              {t("nav.language")}: {i18n.language?.startsWith("de") ? "DE" : "EN"}
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/agent/*" element={<AgentPage />} />
          <Route path="/portal/*" element={<PortalPage />} />
          <Route path="/admin/*" element={<AdminPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <Shell />
    </ThemeProvider>
  );
}
