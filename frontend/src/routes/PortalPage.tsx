import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";

export default function PortalPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-lg space-y-3 px-4 py-16 text-center">
      <h1 className="font-display text-2xl font-semibold text-ink">{t("portal.title")}</h1>
      <p className="text-muted">{t("portal.placeholder")}</p>
      <Link to="/" className="text-sm text-accent hover:underline">
        {t("nav.home")}
      </Link>
    </div>
  );
}
