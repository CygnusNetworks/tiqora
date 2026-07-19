import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";

export default function AdminPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-lg space-y-3 px-4 py-16 text-center">
      <h1 className="text-2xl font-semibold">{t("admin.title")}</h1>
      <p className="text-muted">{t("admin.placeholder")}</p>
      <Link to="/" className="text-sm text-accent hover:underline">
        {t("nav.home")}
      </Link>
    </div>
  );
}
