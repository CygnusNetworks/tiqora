import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useTheme } from "@/themes/theme";
import { Button } from "@/components/ui/Button";
import { SelectMenu } from "@/components/ui/SelectMenu";
import { getLocale, localePickerItems, resolveLocaleCode, setAppLanguage } from "@/i18n";
import { cn } from "@/lib/cn";

/**
 * Agent preferences hub reached from the sidebar user card. Composes the
 * language + theme controls (the same ones previously only living in the
 * header toolbar) and links out to the dedicated security/2FA page rather
 * than duplicating its TOTP flow here.
 */
export function SettingsPage() {
  const { t, i18n } = useTranslation();
  const { theme, setTheme } = useTheme();

  const currentLang = resolveLocaleCode(i18n.language);
  const languageItems = localePickerItems();
  const currentLabel =
    getLocale(currentLang)?.label ?? languageItems.find((l) => l.value === currentLang)?.label ?? currentLang;

  return (
    <div className="mx-auto w-full max-w-2xl space-y-6 px-4 py-6" data-testid="settings-page">
      <h1 className="font-display text-2xl font-bold tracking-tight text-ink">
        {t("settings.title")}
      </h1>

      <section className="space-y-2 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("settings.language")}</h2>
        <SelectMenu
          items={languageItems}
          value={currentLang}
          onSelect={(code) => void setAppLanguage(code)}
          panelTestId="settings-lang-panel"
          trigger={({ open, ref, toggleProps }) => (
            <button
              ref={ref}
              type="button"
              data-testid="settings-lang-select"
              aria-label={t("settings.language")}
              {...toggleProps}
              className={cn(
                "flex w-full max-w-sm items-center justify-between rounded-lg border border-hairline bg-bg px-3 py-2 text-left text-sm text-ink transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                open && "bg-surface-subtle",
              )}
            >
              <span className="truncate">{currentLabel}</span>
              <span
                className={cn("text-muted transition-transform duration-150", open && "rotate-180")}
                aria-hidden
              >
                ⌄
              </span>
            </button>
          )}
        />
        {/* Keep legacy test hooks for the two fully translated day-one locales. */}
        <div className="flex gap-2">
          <Button
            variant={currentLang === "en" ? "primary" : "secondary"}
            size="sm"
            data-testid="settings-lang-en"
            onClick={() => void setAppLanguage("en")}
          >
            {t("settings.langEnglish")}
          </Button>
          <Button
            variant={currentLang === "de" ? "primary" : "secondary"}
            size="sm"
            data-testid="settings-lang-de"
            onClick={() => void setAppLanguage("de")}
          >
            {t("settings.langGerman")}
          </Button>
        </div>
      </section>

      <section className="space-y-2 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("settings.theme")}</h2>
        <div className="flex gap-2">
          <Button
            variant={theme === "light" ? "primary" : "secondary"}
            size="sm"
            data-testid="settings-theme-light"
            onClick={() => setTheme("light")}
          >
            {t("settings.themeLight")}
          </Button>
          <Button
            variant={theme === "dark" ? "primary" : "secondary"}
            size="sm"
            data-testid="settings-theme-dark"
            onClick={() => setTheme("dark")}
          >
            {t("settings.themeDark")}
          </Button>
        </div>
      </section>

      <section className="space-y-2 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("settings.security")}</h2>
        <p className="text-[13px] text-muted">{t("settings.securityHint")}</p>
        <Link
          to="/agent/security"
          data-testid="settings-security-link"
          className="inline-flex text-sm font-medium text-accent hover:underline"
        >
          {t("settings.securityLink")}
        </Link>
      </section>
    </div>
  );
}
