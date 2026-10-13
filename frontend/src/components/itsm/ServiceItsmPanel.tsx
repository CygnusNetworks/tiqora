import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CiStateBadge } from "@/components/itsm/CiStateBadge";
import { ciDetailHref } from "@/lib/itsmRoutes";
import { itsmApi, itsmKeys, type CiSource } from "@/lib/itsmApi";
import { RouterAnchor } from "@/components/ui/RouterAnchor";

/**
 * ITSM facts of one service in the service view: live incident state, owner, type, criticality,
 * description and the CIs allocated to it. Renders nothing while the ITSM is switched off.
 */
export function ServiceItsmPanel({ serviceId }: { serviceId: number }) {
  const { t } = useTranslation();
  const statusQ = useQuery({
    queryKey: itsmKeys.status(),
    queryFn: ({ signal }) => itsmApi.status(signal),
    retry: false,
    staleTime: 60_000,
  });
  const on = !!statusQ.data && statusQ.data.mode !== "off";
  const serviceQ = useQuery({
    queryKey: itsmKeys.service(serviceId),
    queryFn: ({ signal }) => itsmApi.service(serviceId, signal),
    retry: false,
    enabled: on,
  });
  const cisQ = useQuery({
    queryKey: itsmKeys.serviceCis(serviceId),
    queryFn: ({ signal }) => itsmApi.serviceCis(serviceId, undefined, signal),
    retry: false,
    enabled: on,
  });
  if (!on) return null;
  if (serviceQ.isError) {
    return (
      <p className="rounded-lg border border-hairline bg-surface px-4 py-3 text-sm text-danger" role="alert" data-testid="service-itsm-error">
        {t("itsm.service.loadError")}
      </p>
    );
  }
  const svc = serviceQ.data;
  if (!svc) return null;
  const cis = cisQ.data ?? [];
  const facts: [string, string, string | null][] = [
    ["owner", t("itsm.service.owner"), svc.owner_name],
    ["type", t("itsm.service.type"), svc.service_type],
    ["criticality", t("itsm.service.criticality"), svc.criticality],
  ];
  return (
    <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="service-itsm">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-semibold text-ink">{t("itsm.service.title")}</h2>
        <span className="flex items-center gap-1 text-xs text-muted">
          {t("itsm.service.incidentState")}
          <CiStateBadge state={svc.incident_state?.state} />
        </span>
      </div>
      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
        {facts.map(([key, label, value]) => (
          <div key={key} data-testid={`service-itsm-${key}`}>
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="text-ink">{value || "–"}</dd>
          </div>
        ))}
      </dl>
      {svc.description && (
        <p className="whitespace-pre-wrap text-sm text-ink" data-testid="service-itsm-description">
          {svc.description}
        </p>
      )}
      <div>
        <h3 className="mb-1 text-xs font-medium text-muted">{t("itsm.service.cis")}</h3>
        {cis.length === 0 ? (
          <p className="text-sm text-muted" data-testid="service-itsm-no-cis">
            {t("itsm.service.noCis")}
          </p>
        ) : (
          <ul className="divide-y divide-hairline" data-testid="service-itsm-cis">
            {cis.map((ci) => (
              <li key={ci.ref} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-sm" data-testid={`service-ci-${ci.id}`}>
                <RouterAnchor href={ciDetailHref(ci.source as CiSource, ci.id)} className="font-mono text-xs text-accent hover:underline">
                  {ci.number}
                </RouterAnchor>
                <span className="min-w-0 flex-1 truncate text-ink">{ci.name}</span>
                <span className="text-xs text-muted">{ci.class_name}</span>
                <CiStateBadge state={ci.incident_state} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
