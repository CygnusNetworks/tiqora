import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { portalCmdbApi } from "@/lib/portalCmdbApi";
import { Spinner } from "@/components/ui/Spinner";
import { CiAttributeView } from "@/components/cmdb/CiAttributeView";
import { CiStateBadge } from "@/components/cmdb/CiStateBadge";

export function PortalCiPage() {
  const { t } = useTranslation();
  const { ciId } = useParams({ strict: false }) as { ciId: string };
  const id = Number(ciId);
  const ci = useQuery({
    queryKey: ["portal", "cmdb", "detail", id],
    queryFn: () => portalCmdbApi.get(id),
    enabled: Number.isInteger(id),
    retry: false,
  });

  return (
    <div className="space-y-4" data-testid="portal-ci-page">
      <Link
        to="/portal/config-items"
        className="text-sm text-muted hover:text-ink"
        data-testid="portal-ci-back"
      >
        ← {t("portal.cmdb.back")}
      </Link>
      {ci.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : ci.isError || !ci.data ? (
        <p className="text-sm text-danger" role="alert" data-testid="portal-ci-error">
          {t("portal.cmdb.notFound")}
        </p>
      ) : (
        <>
          <div className="space-y-2">
            <h1 className="break-words font-display text-xl font-semibold text-ink">{ci.data.name}</h1>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <span>{ci.data.class_name}</span>
              <span>· {ci.data.number}</span>
              <CiStateBadge kind="deployment" functionality={null} label={ci.data.deployment_state} />
              <CiStateBadge
                kind="incident"
                functionality={ci.data.incident_functionality}
                label={ci.data.incident_state}
              />
            </div>
          </div>
          <section className="rounded-lg border border-hairline bg-surface p-4">
            {ci.data.rendered.length === 0 ? (
              <p className="text-sm text-muted" data-testid="portal-ci-no-attributes">
                {t("portal.cmdb.noAttributes")}
              </p>
            ) : (
              <CiAttributeView rendered={ci.data.rendered} />
            )}
          </section>
          <Link
            to="/portal/tickets/new"
            search={{ ci: ci.data.id }}
            className="inline-flex rounded-md border border-transparent bg-accent px-3 py-1.5 text-sm font-medium text-accent-ink hover:opacity-90"
            data-testid="portal-ci-create-ticket"
          >
            {t("portal.cmdb.createTicket")}
          </Link>
        </>
      )}
    </div>
  );
}
