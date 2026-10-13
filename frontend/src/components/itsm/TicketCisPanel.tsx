import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CiPicker } from "@/components/itsm/CiPicker";
import { CiStateBadge } from "@/components/itsm/CiStateBadge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import type { TicketDetail } from "@/lib/api";
import { ciDetailHref } from "@/lib/itsmRoutes";
import { safeHref } from "@/lib/ciDisplay";
import { itsmApi, itsmErrorKey, itsmKeys, type CiTicketLink } from "@/lib/itsmApi";
import { apiErrorMessage } from "@/lib/avatarApi";
import { ticketPerms } from "@/lib/ticket";

/**
 * "Betroffene CIs" of the ticket zoom: the linked configuration items with their states, linking
 * through `CiPicker`, removal, and (Mode A) the edit link into Znuny. Renders only while
 * `GET /tickets/{id}/itsm` says `enabled`; a CMDB that is unavailable is a plain message.
 */
export function TicketCisPanel({ ticket }: { ticket: TicketDetail }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const ticketId = ticket.id;
  const [picking, setPicking] = useState(false);
  const [pickError, setPickError] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const itsmQ = useQuery({
    queryKey: itsmKeys.ticketItsm(ticketId),
    queryFn: ({ signal }) => itsmApi.ticketItsm(ticketId, signal),
    retry: false,
  });
  const enabled = itsmQ.data?.enabled === true;
  const modeA = itsmQ.data?.mode === "znuny_readthrough";
  const canWrite = ticketPerms(ticket).rw;

  const cisQ = useQuery({
    queryKey: itsmKeys.ticketCis(ticketId),
    queryFn: ({ signal }) => itsmApi.ticketCis(ticketId, signal),
    retry: false,
    enabled,
  });
  const links = useMemo(() => cisQ.data ?? [], [cisQ.data]);
  const linkedRefs = useMemo(() => new Set(links.map((l) => l.ci.ref)), [links]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: itsmKeys.ticketCis(ticketId) });
    void qc.invalidateQueries({ queryKey: itsmKeys.ticketItsm(ticketId) });
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "link-graph"] });
  };

  const link = useMutation({
    mutationFn: (v: { ref: string; linkType: string | null }) => itsmApi.linkCi(ticketId, { ci: v.ref, link_type: v.linkType }),
    onSuccess: () => {
      setPicking(false);
      setPickError(null);
      refresh();
    },
    onError: (err) => setPickError(apiErrorMessage(err, t("itsm.zoom.cis.linkError"))),
  });
  const unlink = useMutation({
    mutationFn: (l: CiTicketLink) => itsmApi.unlinkCi(ticketId, l.ci.source, l.ci.id, l.link_type),
    onSuccess: () => {
      setRemoveError(null);
      refresh();
    },
    onError: (err) => setRemoveError(apiErrorMessage(err, t("itsm.zoom.cis.removeError"))),
  });

  if (!enabled) return null;

  return (
    <section
      className="space-y-2 rounded-lg border border-hairline bg-surface p-3 text-sm"
      aria-label={t("itsm.zoom.cis.title")}
      data-testid="ticket-cis"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-ink">{t("itsm.zoom.cis.title")}</h2>
        {canWrite && !cisQ.isError && (
          <Button
            size="sm"
            onClick={() => {
              setPickError(null);
              setPicking(true);
            }}
            data-testid="ticket-cis-add"
          >
            {t("itsm.zoom.cis.add")}
          </Button>
        )}
      </div>

      {cisQ.isError ? (
        <p className="text-muted" role="status" data-testid="ticket-cis-unavailable">
          {t(itsmErrorKey(cisQ.error))}
        </p>
      ) : cisQ.isLoading ? (
        <Spinner />
      ) : links.length === 0 ? (
        <p className="text-muted" data-testid="ticket-cis-empty">
          {t("itsm.zoom.cis.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline" data-testid="ticket-cis-list">
          {links.map((l) => {
            const editUrl = modeA ? safeHref(l.ci.edit_url) : null;
            return (
              <li
                key={`${l.ci.ref}:${l.link_type}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5"
                data-testid={`ticket-ci-${l.ci.id}`}
              >
                <a href={ciDetailHref(l.ci.source, l.ci.id)} className="font-mono text-xs text-accent hover:underline">
                  {l.ci.number}
                </a>
                <span className="min-w-0 flex-1 truncate text-ink">{l.ci.name}</span>
                <span className="text-xs text-muted">{l.ci.class_name}</span>
                <span className="flex items-center gap-1 text-xs text-muted">
                  {t("itsm.zoom.cis.deployment")}
                  <CiStateBadge state={l.ci.deployment_state} />
                </span>
                <span className="flex items-center gap-1 text-xs text-muted">
                  {t("itsm.zoom.cis.incident")}
                  <CiStateBadge state={l.ci.incident_state} />
                </span>
                {editUrl && (
                  <a
                    href={editUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-accent hover:underline"
                    data-testid={`ticket-ci-edit-${l.ci.id}`}
                  >
                    {t("itsm.ci.openInZnuny")}
                  </a>
                )}
                {canWrite && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={unlink.isPending}
                    onClick={() => unlink.mutate(l)}
                    aria-label={t("itsm.zoom.cis.remove", { name: l.ci.name })}
                    data-testid={`ticket-ci-remove-${l.ci.id}`}
                  >
                    {t("itsm.zoom.cis.removeShort")}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {removeError && (
        <p className="text-xs text-danger" role="alert" data-testid="ticket-cis-error">
          {removeError}
        </p>
      )}
      {picking && (
        <CiPicker
          onClose={() => setPicking(false)}
          linked={linkedRefs}
          busy={link.isPending}
          error={pickError}
          onPick={(ci, linkType) => link.mutate({ ref: ci.ref, linkType })}
        />
      )}
    </section>
  );
}
