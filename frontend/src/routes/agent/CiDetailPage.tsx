import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { CiAttributeTree } from "@/components/itsm/CiAttributeTree";
import { CiStateBadge } from "@/components/itsm/CiStateBadge";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { safeHref } from "@/lib/ciDisplay";
import { formatDateTime } from "@/lib/format";
import { itsmApi, itsmErrorCode, itsmErrorKey, itsmKeys, type CiSource } from "@/lib/itsmApi";
import { RouterAnchor } from "@/components/ui/RouterAnchor";

const VERSIONS_LIMIT = 50;

/** `/agent/itsm/cis/$source/$ciId`: one CI with attributes, versions and linked tickets. */
export function CiDetailPage() {
  const { t, i18n } = useTranslation();
  const params = useParams({ from: "/agent/itsm/cis/$source/$ciId" });
  const source: CiSource = params.source === "tiqora" ? "tiqora" : "znuny";
  const ciId = Number(params.ciId);
  const [versionId, setVersionId] = useState<number | null>(null);

  const detailQ = useQuery({
    queryKey: itsmKeys.ci(source, ciId, versionId),
    queryFn: ({ signal }) => itsmApi.ci(source, ciId, versionId, signal),
    retry: false,
    enabled: Number.isInteger(ciId),
    placeholderData: (prev) => prev,
  });
  const versionsQ = useQuery({
    queryKey: itsmKeys.ciVersions(source, ciId),
    queryFn: ({ signal }) => itsmApi.ciVersions(source, ciId, { limit: VERSIONS_LIMIT }, signal),
    retry: false,
    enabled: Number.isInteger(ciId),
  });
  const ticketsQ = useQuery({
    queryKey: itsmKeys.ciTickets(source, ciId),
    queryFn: ({ signal }) => itsmApi.ciTickets(source, ciId, signal),
    retry: false,
    enabled: Number.isInteger(ciId),
  });

  const shell = (children: React.ReactNode) => (
    <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-6" data-testid="ci-detail-page" data-ci={`${source}:${ciId}`}>
      <RouterAnchor href="/agent/itsm/cis" className="text-xs text-accent hover:underline" data-testid="ci-detail-back">
        {t("itsm.ci.back")}
      </RouterAnchor>
      {children}
    </div>
  );

  if (detailQ.isLoading) {
    return shell(
      <div className="flex justify-center py-10">
        <Spinner />
      </div>,
    );
  }
  if (detailQ.isError || !detailQ.data) {
    const missing = itsmErrorCode(detailQ.error) === "ci_not_found";
    return shell(
      <p className="rounded-lg border border-hairline bg-surface px-4 py-6 text-sm text-danger" role="alert" data-testid="ci-detail-error">
        {missing ? t("itsm.ci.notFound") : t(itsmErrorKey(detailQ.error))}
      </p>,
    );
  }

  const { summary, version, attributes } = detailQ.data;
  const versions = versionsQ.data?.items ?? [];
  const currentSeq = versions.reduce((max, v) => Math.max(max, v.seq), 0);
  const shown = version ?? null;
  const viewingOld = shown !== null && currentSeq > 0 && shown.seq !== currentSeq;
  const name = shown?.name ?? summary.name;
  const deployment = shown?.deployment_state ?? summary.deployment_state;
  const incident = shown?.incident_state ?? summary.incident_state;
  const editUrl = detailQ.data.read_only && summary.source === "znuny" ? safeHref(summary.edit_url) : null;
  const tickets = ticketsQ.data ?? [];

  return shell(
    <>
      <header className="space-y-2 rounded-lg border border-hairline bg-surface p-4" data-testid="ci-detail-header">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-mono text-xs text-accent" data-testid="ci-detail-number">
            {summary.number}
          </span>
          <h1 className="font-display text-xl font-semibold text-ink" data-testid="ci-detail-name">
            {name}
          </h1>
          <span className="text-xs text-muted">{summary.class_name}</span>
          {editUrl && (
            <a
              href={editUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto text-xs text-accent hover:underline"
              data-testid="ci-detail-edit"
            >
              {t("itsm.ci.openInZnuny")}
            </a>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
          <span className="flex items-center gap-1">
            {t("itsm.ci.deployment")}
            <CiStateBadge state={deployment} />
          </span>
          <span className="flex items-center gap-1">
            {t("itsm.ci.incident")}
            <CiStateBadge state={incident} />
          </span>
          {detailQ.data.read_only && <span data-testid="ci-detail-readonly">{t("itsm.ci.readOnly")}</span>}
        </div>
      </header>

      {viewingOld && shown && (
        <div
          className="flex flex-wrap items-center gap-3 rounded-lg border border-escalation/30 bg-escalation/10 px-4 py-2 text-sm text-ink"
          data-testid="ci-detail-old-version"
        >
          <span>{t("itsm.ci.viewingVersion", { seq: shown.seq, date: formatDateTime(shown.created_at, i18n.language) })}</span>
          <Button size="sm" data-testid="ci-detail-current" onClick={() => setVersionId(null)}>
            {t("itsm.ci.showCurrent")}
          </Button>
        </div>
      )}

      <section className="rounded-lg border border-hairline bg-surface p-4" data-testid="ci-detail-attributes">
        <h2 className="mb-2 text-sm font-semibold text-ink">{t("itsm.ci.attributes")}</h2>
        <CiAttributeTree attributes={attributes} />
      </section>

      <section className="rounded-lg border border-hairline bg-surface p-4" data-testid="ci-detail-versions">
        <h2 className="mb-2 text-sm font-semibold text-ink">{t("itsm.ci.versions")}</h2>
        {versions.length === 0 ? (
          <p className="text-sm text-muted">{t("itsm.ci.noVersions")}</p>
        ) : (
          <ul className="divide-y divide-hairline">
            {versions.map((v) => {
              const isCurrent = v.seq === currentSeq;
              const isShown = shown ? shown.id === v.id : isCurrent;
              return (
                <li key={v.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-sm" data-testid={`ci-version-${v.id}`}>
                  <span className="font-mono text-xs text-muted">#{v.seq}</span>
                  <span className="text-xs text-muted">{formatDateTime(v.created_at, i18n.language)}</span>
                  {v.created_by_name && <span className="text-xs text-muted">{t("itsm.ci.versionBy", { name: v.created_by_name })}</span>}
                  <CiStateBadge state={v.deployment_state} />
                  <CiStateBadge state={v.incident_state} />
                  {v.change_note && <span className="min-w-0 flex-1 truncate text-ink">{v.change_note}</span>}
                  {isCurrent && (
                    <Badge tone="accent" data-testid={`ci-version-current-${v.id}`}>
                      {t("itsm.ci.versionCurrent")}
                    </Badge>
                  )}
                  <span className="ml-auto">
                    {isShown ? (
                      <span className="text-xs text-muted">{t("itsm.ci.versionShown")}</span>
                    ) : (
                      <Button size="sm" data-testid={`ci-version-view-${v.id}`} onClick={() => setVersionId(isCurrent ? null : v.id)}>
                        {t("itsm.ci.viewVersion")}
                      </Button>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="rounded-lg border border-hairline bg-surface p-4" data-testid="ci-detail-tickets">
        <h2 className="mb-2 text-sm font-semibold text-ink">{t("itsm.ci.tickets")}</h2>
        {tickets.length === 0 ? (
          <p className="text-sm text-muted">{t("itsm.ci.noTickets")}</p>
        ) : (
          <ul className="divide-y divide-hairline">
            {tickets.map((l) => (
              <li
                key={`${l.other_ticket_id}:${l.link_type}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-sm"
                data-testid={`ci-ticket-${l.other_ticket_id}`}
              >
                <RouterAnchor href={`/agent/tickets/${l.other_ticket_id}`} className="font-mono text-xs text-accent hover:underline">
                  {l.other_tn ?? `#${l.other_ticket_id}`}
                </RouterAnchor>
                <span className="min-w-0 flex-1 truncate text-ink">{l.other_title}</span>
                {l.other_state && <span className="text-xs text-muted">{l.other_state}</span>}
                <span className="text-xs text-muted">{l.link_type}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>,
  );
}
