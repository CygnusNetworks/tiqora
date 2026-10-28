import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Switch } from "@/components/ui/Switch";
import { SelectField } from "@/components/ui/SelectField";
import { ApprovalStatusBadge } from "@/components/approvals/ApprovalStatusBadge";
import { ApprovalSteps } from "@/components/approvals/ApprovalSteps";
import { inputCls } from "@/components/approvals/pickers";
import { approvalErrorMessage, approvalsApi, approvalsKeys } from "@/lib/approvalsApi";
import { approvalsAdminApi, approvalsAdminKeys, type ApprovalAdminItem } from "@/lib/approvalsAdminApi";
import { formatDateTime } from "@/lib/format";
import { useSubjectTypes } from "./useSubjectTypes";
import { AdminApprovalsHeader } from "./AdminApprovalsHeader";

const PAGE_SIZE = 25;
const STATUSES = ["pending", "approved", "rejected", "cancelled"] as const;

function ItemDetail({ item, onCancelled }: { item: ApprovalAdminItem; onCancelled: () => void }) {
  const { t } = useTranslation();
  const [confirm, setConfirm] = useState(false);
  const detail = useQuery({
    queryKey: approvalsKeys.detail(item.id),
    queryFn: ({ signal }) => approvalsApi.get(item.id, signal),
  });
  const cancel = useMutation({
    mutationFn: () => approvalsApi.cancel(item.id),
    onSuccess: () => {
      setConfirm(false);
      onCancelled();
    },
  });
  return (
    <div className="space-y-3 p-3" data-testid="admin-approval-detail">
      {detail.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="admin-approval-detail-error">
          {approvalErrorMessage(detail.error, t)}
        </p>
      ) : detail.data ? (
        <ApprovalSteps detail={detail.data} />
      ) : (
        <p className="text-sm text-muted">{t("common.loading")}</p>
      )}
      {item.can_cancel && (
        <div className="flex justify-end">
          <Button variant="danger" size="sm" onClick={() => setConfirm(true)} data-testid="admin-approval-cancel">
            {t("approvals.agent.cancel")}
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={confirm}
        title={t("approvals.admin.overview.cancelTitle")}
        message={
          <>
            <p>{t("approvals.admin.overview.cancelMessage", { title: item.title })}</p>
            {cancel.isError && (
              <p className="mt-2 text-danger" role="alert" data-testid="admin-approval-cancel-error">
                {approvalErrorMessage(cancel.error, t)}
              </p>
            )}
          </>
        }
        variant="danger"
        confirmLabel={t("approvals.agent.cancel")}
        pending={cancel.isPending}
        onConfirm={() => cancel.mutate()}
        onCancel={() => {
          cancel.reset();
          setConfirm(false);
        }}
      />
    </div>
  );
}

/** All approval requests of the system with filters; a row opens the steps and, while open, can be withdrawn. */
export function ApprovalsOverviewPage() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const [status, setStatus] = useState("all");
  const [ticket, setTicket] = useState("");
  const [subjectType, setSubjectType] = useState("all");
  const subjects = useSubjectTypes();
  const [overdue, setOverdue] = useState(false);
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<number | null>(null);

  const ticketId = /^\d+$/.test(ticket.trim()) ? Number(ticket.trim()) : undefined;
  const query = {
    status: status === "all" ? undefined : status,
    ticket_id: ticketId,
    subject_type: subjectType === "all" ? undefined : subjectType,
    overdue: overdue || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const q = useQuery({
    queryKey: approvalsAdminKeys.overview(query),
    queryFn: ({ signal }) => approvalsAdminApi.overview(query, signal),
  });
  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "approvals", "overview"] });
    void qc.invalidateQueries({ queryKey: approvalsKeys.all() });
  };
  const filtered = status !== "all" || ticket !== "" || subjectType !== "all" || overdue;
  const changed = (fn: () => void) => {
    fn();
    setPage(0);
    setOpenId(null);
  };

  return (
    <div className="space-y-4 p-4" data-testid="admin-approvals-page">
      <AdminApprovalsHeader title={t("admin.nav.approvals")} />

      <div className="grid grid-cols-1 gap-3 rounded-md border border-hairline bg-surface p-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="admin-approvals-filters">
        <div className="text-xs font-medium text-muted">
          {t("approvals.admin.overview.filter.status")}
          <div className="mt-1">
            <SelectField
              items={[
                { value: "all", label: t("approvals.admin.overview.filter.statusAll") },
                ...STATUSES.map((s) => ({ value: s, label: t(`approvals.status.${s}`) })),
              ]}
              value={status}
              onChange={(v) => changed(() => setStatus(v))}
              testId="admin-approvals-filter-status"
              aria-label={t("approvals.admin.overview.filter.status")}
            />
          </div>
        </div>
        <label className="block text-xs font-medium text-muted">
          {t("approvals.admin.overview.filter.ticketId")}
          <input
            className={`${inputCls} mt-1`}
            inputMode="numeric"
            value={ticket}
            onChange={(e) => changed(() => setTicket(e.target.value))}
            data-testid="admin-approvals-filter-ticket"
          />
        </label>
        <div className="text-xs font-medium text-muted">
          {t("approvals.admin.overview.filter.subjectType")}
          <div className="mt-1">
            <SelectField
              items={[
                { value: "all", label: t("approvals.admin.overview.filter.statusAll") },
                ...subjects.types.map((s) => ({ value: s.key, label: subjects.title(s.key) })),
              ]}
              value={subjectType}
              onChange={(v) => changed(() => setSubjectType(v))}
              testId="admin-approvals-filter-subject"
              aria-label={t("approvals.admin.overview.filter.subjectType")}
            />
          </div>
        </div>
        <label className="flex items-center gap-2 self-end pb-1.5 text-sm text-ink">
          <Switch
            checked={overdue}
            onChange={(v) => changed(() => setOverdue(v))}
            testId="admin-approvals-filter-overdue"
            aria-label={t("approvals.admin.overview.filter.overdue")}
          />
          {t("approvals.admin.overview.filter.overdue")}
        </label>
      </div>

      {q.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="admin-approvals-error">
          {t("approvals.agent.loadFailed")}
        </p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted" data-testid="admin-approvals-empty">
          {q.isPending ? t("common.loading") : filtered ? t("approvals.admin.overview.emptyFiltered") : t("approvals.admin.overview.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline rounded-md border border-hairline bg-surface" data-testid="admin-approvals-list">
          {items.map((it) => (
            <li key={it.id} data-testid="admin-approval-row" data-approval-id={it.id}>
              <button
                type="button"
                className="grid w-full grid-cols-1 gap-1 px-3 py-2 text-left hover:bg-surface-subtle sm:grid-cols-[1fr_auto]"
                aria-expanded={openId === it.id}
                onClick={() => setOpenId(openId === it.id ? null : it.id)}
                data-testid="admin-approval-toggle"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-ink">{it.title}</span>
                  <span className="block truncate text-xs text-muted">
                    {t("approvals.agent.ticket", { number: it.ticket_number ?? it.ticket_id })}
                    {it.ticket_title ? ` – ${it.ticket_title}` : ""}
                    {" · "}
                    {subjects.title(it.subject_type)}
                    {" · "}
                    {t("approvals.agent.requestedBy", { name: it.requester_label })}
                    {" · "}
                    {formatDateTime(it.created_at, i18n.language)}
                  </span>
                  {it.step_name && it.status === "pending" && (
                    <span className="block truncate text-xs text-muted" data-testid="admin-approval-step">
                      {it.step_name}
                      {" · "}
                      {t("approvals.admin.overview.openApprovers", { count: it.open_approvers })}
                    </span>
                  )}
                </span>
                <ApprovalStatusBadge status={it.status} overdue={it.overdue} />
              </button>
              {openId === it.id && <ItemDetail item={it} onCancelled={refresh} />}
            </li>
          ))}
        </ul>
      )}

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-muted" data-testid="admin-approvals-paging">
          <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)} data-testid="admin-approvals-prev">
            {t("approvals.agent.prev")}
          </Button>
          <span>{t("approvals.agent.pageOf", { page: page + 1, pages })}</span>
          <Button size="sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)} data-testid="admin-approvals-next">
            {t("approvals.agent.next")}
          </Button>
        </div>
      )}
    </div>
  );
}
