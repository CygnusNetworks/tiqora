import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { portalCmdbApi } from "@/lib/portalCmdbApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { CiStateBadge } from "@/components/cmdb/CiStateBadge";

export function PortalCisPage() {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["portal", "cmdb", "list", q],
    queryFn: () => portalCmdbApi.list({ q: q || undefined, limit: 100 }),
    retry: false,
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setQ(draft.trim());
  };

  return (
    <div className="space-y-4" data-testid="portal-cis-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("portal.cmdb.title")}</h1>
      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          data-testid="portal-cis-search"
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t("portal.cmdb.searchPlaceholder")}
          aria-label={t("portal.cmdb.searchPlaceholder")}
          className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
        />
        <Button type="submit" variant="primary" data-testid="portal-cis-search-submit">
          {t("search.submit")}
        </Button>
      </form>
      {list.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : list.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="portal-cis-error">
          {t("portal.cmdb.loadError")}
        </p>
      ) : (list.data?.items.length ?? 0) === 0 ? (
        <div
          className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center"
          data-testid="portal-cis-empty"
        >
          <p className="text-sm text-muted">{t("portal.cmdb.empty")}</p>
        </div>
      ) : (
        <ul className="space-y-2" data-testid="portal-cis-list">
          {list.data?.items.map((ci) => (
            <li key={ci.id}>
              <Link
                to="/portal/config-items/$ciId"
                params={{ ciId: String(ci.id) }}
                className="block rounded-lg border border-hairline bg-surface px-4 py-3 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                data-testid={`portal-ci-${ci.id}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="min-w-0 break-words text-sm font-medium text-ink">{ci.name}</p>
                  <CiStateBadge
                    kind="incident"
                    functionality={ci.incident_functionality}
                    label={ci.incident_state}
                  />
                </div>
                <p className="mt-1 text-xs text-muted">
                  {ci.class_name} · {ci.number}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
