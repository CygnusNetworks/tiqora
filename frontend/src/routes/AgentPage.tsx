import { useTranslation } from "react-i18next";

export default function AgentPage() {
  const { t } = useTranslation();
  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-semibold">{t("agent.title")}</h1>
      <p className="text-muted">{t("agent.placeholder")}</p>
    </section>
  );
}
