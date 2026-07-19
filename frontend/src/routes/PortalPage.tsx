import { useTranslation } from "react-i18next";

export default function PortalPage() {
  const { t } = useTranslation();
  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-semibold">{t("portal.title")}</h1>
      <p className="text-muted">{t("portal.placeholder")}</p>
    </section>
  );
}
