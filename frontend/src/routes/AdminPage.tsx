import { useTranslation } from "react-i18next";

export default function AdminPage() {
  const { t } = useTranslation();
  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-semibold">{t("admin.title")}</h1>
      <p className="text-muted">{t("admin.placeholder")}</p>
    </section>
  );
}
