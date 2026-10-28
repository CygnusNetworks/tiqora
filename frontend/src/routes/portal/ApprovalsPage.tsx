import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { APPROVALS_PAGE_SIZE } from "@/lib/approvalsApi";
import { portalApprovalsApi, portalApprovalsKeys } from "@/lib/portalApprovalsApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { ApprovalStatusBadge } from "@/components/approvals/ApprovalStatusBadge";

type PortalTab = "pending" | "decided";

/** The customer's approvals: open ones and decided ones (paged in SQL); a 404 means the feature is off. */
export function PortalApprovalsPage() {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState<PortalTab>("pending");
  const [page, setPage] = useState(0);
  const list = useQuery({
    queryKey: portalApprovalsKeys.list(tab, page),
    queryFn: ({ signal }) =>
      portalApprovalsApi.list({ state: tab, limit: APPROVALS_PAGE_SIZE, offset: page * APPROVALS_PAGE_SIZE }, signal),
    retry: false,
  });
  const unavailable = list.error instanceof ApiError && list.error.status === 404;
  const pages = Math.max(1, Math.ceil((list.data?.total ?? 0) / APPROVALS_PAGE_SIZE));

  return (
    <div className="space-y-4" data-testid="portal-approvals-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("approvals.portal.title")}</h1>
      {unavailable ? (
        <p className="text-sm text-muted" role="alert" data-testid="portal-approvals-unavailable">
          {t("approvals.portal.unavailable")}
        </p>
      ) : (
        <>
          <Tabs
            ariaLabel={t("approvals.portal.title")}
            value={tab}
            onChange={(id) => {
              setTab(id as PortalTab);
              setPage(0);
            }}
            items={[
              { id: "pending", label: t("approvals.agent.tabs.open") },
              { id: "decided", label: t("approvals.agent.tabs.done") },
            ]}
          />
          {list.isLoading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : list.isError ? (
            <p className="text-sm text-danger" role="alert" data-testid="portal-approvals-error">
              {t("approvals.portal.loadError")}
            </p>
          ) : (list.data?.items.length ?? 0) === 0 ? (
            <div
              className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center"
              data-testid="portal-approvals-empty"
            >
              <p className="text-sm text-muted">
                {tab === "pending" ? t("approvals.portal.empty") : t("approvals.portal.emptyDone")}
              </p>
            </div>
          ) : (
            <>
              <ul className="space-y-2" data-testid="portal-approvals-list">
                {list.data?.items.map((item) => (
                  <li key={item.id}>
                    <Link
                      to="/portal/approvals/$approvalId"
                      params={{ approvalId: String(item.id) }}
                      className="block rounded-lg border border-hairline bg-surface px-4 py-3 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                      data-testid={`portal-approval-${item.id}`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="min-w-0 break-words text-sm font-medium text-ink">{item.title}</p>
                        <ApprovalStatusBadge status={tab === "pending" ? item.my_status : item.status} />
                      </div>
                      <p className="mt-1 text-xs text-muted">
                        {item.ticket_number ? `#${item.ticket_number} · ` : ""}
                        {item.step_name}
                        {tab === "pending" && item.due_at && (
                          <> · {t("approvals.steps.due", { date: formatDateTime(item.due_at, i18n.language) })}</>
                        )}
                        {tab === "decided" && item.decided_at && (
                          <> · {t("approvals.portal.decidedAt", { date: formatDateTime(item.decided_at, i18n.language) })}</>
                        )}
                      </p>
                    </Link>
                  </li>
                ))}
              </ul>
              {pages > 1 && (
                <div className="flex items-center justify-between gap-2" data-testid="portal-approvals-pager">
                  <Button disabled={page === 0} onClick={() => setPage(page - 1)} data-testid="portal-approvals-prev">
                    {t("approvals.agent.prev")}
                  </Button>
                  <span className="text-xs text-muted">{t("approvals.agent.pageOf", { page: page + 1, pages })}</span>
                  <Button
                    disabled={page + 1 >= pages}
                    onClick={() => setPage(page + 1)}
                    data-testid="portal-approvals-next"
                  >
                    {t("approvals.agent.next")}
                  </Button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
