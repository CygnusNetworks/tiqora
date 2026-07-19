import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

export default function HomePage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">{t("app.name")}</h1>
        <p className="mt-2 text-muted">{t("app.tagline")}</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        {(
          [
            ["agent", "/agent"],
            ["portal", "/portal"],
            ["admin", "/admin"],
          ] as const
        ).map(([key, to]) => (
          <Link
            key={key}
            to={to}
            className="rounded-lg border border-border bg-surface-elevated p-5 shadow-sm transition hover:border-accent"
          >
            <h2 className="font-medium text-ink">{t(`nav.${key}`)}</h2>
            <p className="mt-2 text-sm text-muted">{t(`${key}.placeholder`)}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}
