import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useTheme } from "@/themes/theme";
import { Button } from "@/components/ui/Button";

/**
 * Agent preferences hub reached from the sidebar user card. Composes the
 * language + theme controls (the same ones previously only living in the
 * header toolbar) and links out to the dedicated security/2FA page rather
 * than duplicating its TOTP flow here.
 */
export function SettingsPage() {
  const { t, i18n } = useTranslation();
  const { theme, setTheme } = useTheme();

  const isDe = i18n.language?.startsWith("de");

  const changeLang = (lang: "en" | "de") => {
    void i18n.changeLanguage(lang);
    localStorage.setItem("tiqora-lang", lang);
  };

  return (
    <div className="mx-auto w-full max-w-2xl space-y-6 px-4 py-6" data-testid="settings-page">
      <h1 className="font-display text-2xl font-bold tracking-tight text-ink">
        {t("settings.title")}
      </h1>

      <section className="space-y-2 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("settings.language")}</h2>
        <div className="flex gap-2">
          <Button
            variant={isDe ? "secondary" : "primary"}
            size="sm"
            data-testid="settings-lang-en"
            onClick={() => changeLang("en")}
          >
            {t("settings.langEnglish")}
          </Button>
          <Button
            variant={isDe ? "primary" : "secondary"}
            size="sm"
            data-testid="settings-lang-de"
            onClick={() => changeLang("de")}
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
