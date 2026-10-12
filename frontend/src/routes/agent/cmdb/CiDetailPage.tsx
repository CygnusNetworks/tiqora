import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { lazy, Suspense, useState } from "react";
import { useTranslation } from "react-i18next";
import { AffectedPanel } from "@/components/cmdb/AffectedPanel";
import { CiAttributeView } from "@/components/cmdb/CiAttributeView";
import { CiHistoryTable } from "@/components/cmdb/CiHistoryTable";
import { CiLinksPanel } from "@/components/cmdb/CiLinksPanel";
import { CiStateBadge } from "@/components/cmdb/CiStateBadge";
import { CiVersionsPanel } from "@/components/cmdb/CiVersionsPanel";
import { CmdbModeNotice } from "@/components/cmdb/CmdbModeNotice";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { ApiError } from "@/lib/api";
import { cmdbApi, useCmdbStatus, type Ci } from "@/lib/cmdbApi";
import { CI_DETAIL_TABS, validateCiDetailSearch, type CiDetailSearch, type CiDetailTab } from "@/components/cmdb/ciDetailModel";

// The graph library stays out of the main bundle: the chunk loads with the "Abhängigkeiten" tab.
const ImpactGraph = lazy(() => import("@/components/cmdb/ImpactGraph"));

const LINK_BTN =
  "inline-flex items-center rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-subtle";

function TicketsTab({ ciId }: { ciId: number }) {
  const { t } = useTranslation();
  const [openOnly, setOpenOnly] = useState(false);
  const q = useQuery({ queryKey: ["cmdb", "ci", ciId, "tickets", openOnly], queryFn: () => cmdbApi.tickets(ciId, openOnly), placeholderData: keepPreviousData });
  return (
    <div className="space-y-3">
      <label className="inline-flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} data-testid="ci-tickets-open-only" />
        {t("cmdb.detail.tickets.openOnly")}
      </label>
      {q.isError && (
        <p role="alert" className="text-sm text-danger">
          {t("cmdb.detail.tickets.loadFailed")}
        </p>
      )}
      {q.isPending && <Spinner />}
      {q.isSuccess && q.data.length === 0 && (
        <p className="text-sm text-muted" data-testid="ci-tickets-empty">
          {t("cmdb.detail.tickets.empty")}
        </p>
      )}
      {q.isSuccess && q.data.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-hairline">
          <table className="w-full text-sm" data-testid="ci-tickets">
            <thead className="bg-surface-subtle text-xs text-muted">
              <tr>
                {(["number", "title", "state", "queue", "linkType"] as const).map((c) => (
                  <th key={c} scope="col" className="px-3 py-2 text-left font-medium">
                    {t(`cmdb.detail.tickets.${c}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {q.data.map((tk) => (
                <tr key={tk.ticket_id} className="border-t border-hairline" data-testid={`ci-ticket-${tk.ticket_id}`}>
                  <td className="px-3 py-2">
                    <Link to="/agent/tickets/$ticketId" params={{ ticketId: String(tk.ticket_id) }} className="font-medium text-accent hover:underline">
                      {tk.number}
                    </Link>
                  </td>
                  <td className="px-3 py-2">{tk.title}</td>
                  <td className="px-3 py-2">{tk.state_name}</td>
                  <td className="px-3 py-2">{tk.queue_name}</td>
                  <td className="px-3 py-2 text-muted">{tk.link_type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Header({ ci }: { ci: Ci }) {
  const { t } = useTranslation();
  const { deployment, incident_own: own, incident_current: current } = ci.states;
  const derived = own.id !== current.id;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-sm text-muted" data-testid="ci-number">
          {ci.number}
        </span>
        <h1 className="font-display text-xl font-bold text-ink" data-testid="ci-name">
          {ci.name}
        </h1>
        <span className="text-sm text-muted" data-testid="ci-class">
          {ci.class.name}
        </span>
      </div>
      <dl className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <div className="flex items-center gap-1.5">
          <dt>{t("cmdb.detail.deployment")}</dt>
          <dd>
            <CiStateBadge kind="deployment" functionality={deployment.functionality} label={deployment.name} />
          </dd>
        </div>
        <div className="flex items-center gap-1.5">
          <dt>{t("cmdb.detail.incidentOwn")}</dt>
          <dd data-testid="ci-incident-own">
            <CiStateBadge kind="incident" functionality={own.functionality} label={own.name} />
          </dd>
        </div>
        <div className="flex items-center gap-1.5">
          <dt>{t("cmdb.detail.incidentCurrent")}</dt>
          <dd data-testid="ci-incident-current" className="flex items-center gap-1.5">
            <CiStateBadge kind="incident" functionality={current.functionality} label={current.name} />
            {derived && (
              <Badge tone="warn" title={t("cmdb.detail.derivedTitle")} data-testid="ci-derived-hint">
                {t("cmdb.detail.derivedHint")}
              </Badge>
            )}
          </dd>
        </div>
      </dl>
    </div>
  );
}

/** CI detail: overview, links, tickets, versions with diff, and the change log. Tab lives in `?tab=`. */
export function CiDetailPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { ciId: rawId } = useParams({ from: "/agent/cmdb/$ciId" });
  const search = useSearch({ from: "/agent/cmdb/$ciId" }) as CiDetailSearch;
  const tab: CiDetailTab = validateCiDetailSearch(search).tab ?? "overview";
  const id = Number(rawId);
  const validId = Number.isInteger(id) && id > 0;
  const statusQ = useCmdbStatus();
  const active = statusQ.data?.mode === "tiqora";

  const ciQ = useQuery({
    queryKey: ["cmdb", "ci", id],
    queryFn: () => cmdbApi.get(id),
    enabled: validId && active,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });
  const [confirmOpen, setConfirmOpen] = useState(false);
  const remove = useMutation({
    mutationFn: () => cmdbApi.remove(id),
    onSuccess: async () => {
      setConfirmOpen(false);
      qc.removeQueries({ queryKey: ["cmdb", "ci", id] });
      await qc.invalidateQueries({ queryKey: ["cmdb", "cis"] });
      void navigate({ to: "/agent/cmdb" });
    },
  });

  const setTab = (next: string) => {
    void navigate({ to: "/agent/cmdb/$ciId", params: { ciId: String(id) }, search: validateCiDetailSearch({ tab: next }), replace: true });
  };

  const backLink = (
    <Link to="/agent/cmdb" className="text-sm text-accent hover:underline" data-testid="ci-back">
      {t("cmdb.detail.back")}
    </Link>
  );

  if (statusQ.data && !active) {
    return (
      <div className="mx-auto max-w-5xl space-y-4 p-4 sm:p-6" data-testid="cmdb-detail-page">
        <CmdbModeNotice status={statusQ.data} />
      </div>
    );
  }
  const notFound = !validId || (ciQ.error instanceof ApiError && ciQ.error.status === 404);
  if (notFound) {
    return (
      <div className="mx-auto max-w-5xl space-y-3 p-4 sm:p-6" data-testid="cmdb-detail-page">
        <p role="alert" className="text-sm text-ink" data-testid="ci-not-found">
          {t("cmdb.detail.notFound")}
        </p>
        {backLink}
      </div>
    );
  }
  if (ciQ.isError) {
    return (
      <div className="mx-auto max-w-5xl space-y-3 p-4 sm:p-6" data-testid="cmdb-detail-page">
        <p role="alert" className="text-sm text-danger">
          {t("cmdb.detail.loadFailed")}
        </p>
        {backLink}
      </div>
    );
  }
  const ci = ciQ.data;
  if (!ci) {
    return (
      <div className="p-6" data-testid="cmdb-detail-page">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4 sm:p-6" data-testid="cmdb-detail-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          {backLink}
          <Header ci={ci} />
        </div>
        {ci.can_write && (
          <div className="flex items-center gap-2">
            <Link to="/agent/cmdb/$ciId/edit" params={{ ciId: String(ci.id) }} className={LINK_BTN} data-testid="ci-edit">
              {t("cmdb.detail.edit")}
            </Link>
            <Button variant="danger" onClick={() => setConfirmOpen(true)} data-testid="ci-delete">
              {t("cmdb.detail.delete")}
            </Button>
          </div>
        )}
      </div>

      <Tabs
        className="[&>button]:min-h-9"
        value={tab}
        onChange={setTab}
        items={CI_DETAIL_TABS.map((id2) => ({ id: id2, label: t(`cmdb.detail.tabs.${id2}`) }))}
      />

      <div data-testid={`ci-tab-${tab}`}>
        {tab === "overview" && <CiAttributeView rendered={ci.rendered} />}
        {tab === "links" && <CiLinksPanel ciId={ci.id} canWrite={ci.can_write} />}
        {tab === "dependencies" && (
          <div className="space-y-6">
            <Suspense fallback={<Spinner />}>
              <ImpactGraph ciId={ci.id} />
            </Suspense>
            <AffectedPanel ciId={ci.id} />
          </div>
        )}
        {tab === "tickets" && <TicketsTab ciId={ci.id} />}
        {tab === "versions" && <CiVersionsPanel ciId={ci.id} />}
        {tab === "history" && <CiHistoryTable ciId={ci.id} />}
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title={t("cmdb.detail.deleteTitle")}
        message={t("cmdb.detail.deleteMessage", { number: ci.number, name: ci.name })}
        confirmLabel={t("cmdb.detail.deleteConfirm")}
        variant="danger"
        role="alertdialog"
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
        onCancel={() => setConfirmOpen(false)}
      >
        {remove.isError && (
          <p role="alert" className="text-sm text-danger">
            {t("cmdb.detail.deleteFailed")}
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}
