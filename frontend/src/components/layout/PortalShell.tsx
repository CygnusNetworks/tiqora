import type { ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useCustomerAuth } from "@/auth/CustomerAuthContext";
import { useTheme } from "@/themes/theme";
import { Button } from "@/components/ui/Button";

export function PortalShell({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
  const { customer, logout } = useCustomerAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();

  const switchLang = () => {
    const next = i18n.language?.startsWith("de") ? "en" : "de";
    void i18n.changeLanguage(next);
    localStorage.setItem("tiqora-lang", next);
  };

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <header className="sticky top-0 z-20 border-b border-hairline bg-surface">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-3 px-4 py-3">
          <Link
            to="/portal"
            className="flex min-w-0 flex-col leading-tight"
            data-testid="portal-home-link"
          >
            <span className="font-display text-lg font-bold tracking-tight text-ink">
              {t("app.name")}
            </span>
            <span className="text-xs text-muted">{t("portal.subtitle")}</span>
          </Link>
          <div className="ml-auto flex items-center gap-1.5 text-sm">
            <Button variant="ghost" size="sm" onClick={toggleTheme}>
              {theme === "dark" ? "☀" : "☾"}
            </Button>
            <Button variant="ghost" size="sm" onClick={switchLang}>
              {i18n.language?.startsWith("de") ? "DE" : "EN"}
            </Button>
            {customer && (
              <span
                className="hidden max-w-[10rem] truncate text-xs text-muted sm:inline"
                data-testid="portal-current-customer"
                title={customer.email}
              >
                {customer.first_name || customer.login}
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              data-testid="portal-logout-btn"
              onClick={() => {
                void logout().then(() => navigate({ to: "/portal/login" }));
              }}
            >
              {t("auth.logout")}
            </Button>
          </div>
        </div>
        <nav className="mx-auto flex max-w-3xl gap-1 px-4 pb-2 text-sm">
          <Link
            to="/portal"
            className="rounded px-2.5 py-1.5 text-muted transition-colors duration-100 hover:bg-surface-subtle hover:text-ink"
            activeProps={{ className: "bg-surface-subtle text-accent" }}
            activeOptions={{ exact: true }}
          >
            {t("portal.nav.myTickets")}
          </Link>
          <Link
            to="/portal/tickets/new"
            className="rounded px-2.5 py-1.5 text-muted transition-colors duration-100 hover:bg-surface-subtle hover:text-ink"
            activeProps={{ className: "bg-surface-subtle text-accent" }}
          >
            {t("portal.nav.newTicket")}
          </Link>
          <Link
            to="/portal/kb"
            className="rounded px-2.5 py-1.5 text-muted transition-colors duration-100 hover:bg-surface-subtle hover:text-ink"
            activeProps={{ className: "bg-surface-subtle text-accent" }}
          >
            {t("portal.nav.kb")}
          </Link>
        </nav>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 animate-route-in px-4 py-6">
        {children}
      </main>
    </div>
  );
}
