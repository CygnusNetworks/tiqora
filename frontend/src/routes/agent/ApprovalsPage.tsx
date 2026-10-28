import { useEffect, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Tabs } from "@/components/ui/Tabs";
import { ApprovalAuditList } from "@/components/approvals/ApprovalAuditList";
import { ApprovalStatusBadge } from "@/components/approvals/ApprovalStatusBadge";
import { ApprovalSteps } from "@/components/approvals/ApprovalSteps";
import { DecisionDialog } from "@/components/approvals/DecisionDialog";
import { DelegationCard } from "@/components/approvals/DelegationCard";
import { ForwardDialog } from "@/components/approvals/ForwardDialog";
import { pickMyRow } from "@/components/approvals/myRow";
import { WithdrawConfirm } from "@/components/approvals/WithdrawConfirm";
import { useApprovalsCount } from "@/components/approvals/useApprovalsCount";
import { usePhoneWidth } from "@/components/process-designer/usePhoneWidth";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import {
  APPROVALS_PAGE_SIZE,
  approvalErrorMessage,
  approvalsApi,
  approvalsKeys,
  type ApprovalDecision,
  type ApprovalDetail,
  type ApprovalInboxItem,
  type ApprovalsTab,
} from "@/lib/approvalsApi";
import type { ApprovalsSearch } from "./approvalsSearch";

/** Overdue first, then the nearest deadline; requests without a deadline last. */
function byDeadline(a: ApprovalInboxItem, b: ApprovalInboxItem): number {
  if (a.overdue !== b.overdue) return a.overdue ? -1 : 1;
  const ad = a.due_at ? new Date(a.due_at).getTime() : Infinity;
  const bd = b.due_at ? new Date(b.due_at).getTime() : Infinity;
  return ad === bd ? 0 : ad < bd ? -1 : 1;
}

/** "nicht möglich (bereits abgestimmt)" / "(eigene Anfrage)" — the F8 reasons of the inbox API. */
const blockedKey = (reason: string | null | undefined) =>
  `approvals.agent.blocked.${reason === "own_request" ? "own_request" : "already_voted"}`;

function InboxRow({ item, selected, onSelect }: { item: ApprovalInboxItem; selected: boolean; onSelect: () => void }) {
  const { t, i18n } = useTranslation();
  const blocked = !item.can_vote && item.status === "pending" && item.my_decision == null;
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        className={cn(
          "block w-full min-w-0 rounded-md border p-3 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
          selected ? "border-accent bg-surface-subtle" : "border-hairline bg-surface hover:bg-surface-subtle",
        )}
        data-testid="approval-row"
        data-approval-id={item.approval_id}
        data-approver-id={item.approver_id}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="min-w-0 break-words text-sm font-medium text-ink" data-testid="approval-row-title">
            {item.title}
          </span>
          {item.overdue ? (
            <Badge tone="danger" data-testid="approval-row-overdue">
              {t("approvals.overdue")}
            </Badge>
          ) : (
            item.status !== "pending" && <ApprovalStatusBadge status={item.status} />
          )}
        </div>
        <p className="mt-1 text-xs text-muted">
          {item.ticket_number ? `${item.ticket_number} · ` : ""}
          {item.step_name}
          {item.due_at && (
            <>
              {" · "}
              <span data-testid="approval-row-due">
                {t("approvals.steps.due", { date: formatDateTime(item.due_at, i18n.language) })}
              </span>
            </>
          )}
        </p>
        {item.on_behalf_of_label && (
          <p className="mt-0.5 text-xs text-muted" data-testid="approval-row-on-behalf">
            {t("approvals.agent.onBehalfOf", { name: item.on_behalf_of_label })}
          </p>
        )}
        {blocked && (
          <p className="mt-0.5 text-xs text-muted" data-testid="approval-row-blocked">
            {t(blockedKey(item.blocked_reason))}
          </p>
        )}
        {item.my_decision && (
          <p className="mt-0.5 text-xs text-muted" data-testid="approval-row-my-decision">
            {t("approvals.agent.myDecision", {
              decision: t(`approvals.status.${item.my_decision}`, { defaultValue: item.my_decision }),
            })}
          </p>
        )}
      </button>
    </li>
  );
}

function DetailPanel({
  id,
  item,
  onClose,
  phone,
}: {
  id: number;
  item: ApprovalInboxItem | undefined;
  onClose: () => void;
  phone: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [decision, setDecision] = useState<ApprovalDecision | null>(null);
  const [forwarding, setForwarding] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const q = useQuery({
    queryKey: approvalsKeys.detail(id),
    queryFn: ({ signal }) => approvalsApi.get(id, signal),
    retry: false,
  });
  const detail = q.data;

  const changed = (d: ApprovalDetail) => {
    setDecision(null);
    setForwarding(false);
    setConfirmCancel(false);
    qc.setQueryData(approvalsKeys.detail(d.id), d);
    void qc.invalidateQueries({ queryKey: approvalsKeys.all() });
    void qc.invalidateQueries({ queryKey: approvalsKeys.ticket(d.ticket_id) });
  };

  const cancel = useMutation({ mutationFn: () => approvalsApi.cancel(id), onSuccess: changed });

  // The detail API lists only rows the caller can vote on right now (blocked ones are absent), so a
  // blocked reason comes from the inbox row; a deep link without one gets a neutral note.
  const row = pickMyRow(detail?.my_rows, item?.approver_id);
  const pending = detail?.status === "pending";
  const canVote = pending && row !== undefined;

  const body = (
    <div className="space-y-4" data-testid="approval-detail" data-approval-id={id}>
      {q.isError && (
        <p className="text-sm text-danger" role="alert" data-testid="approval-detail-error">
          {approvalErrorMessage(q.error, t)}
        </p>
      )}
      {!detail && !q.isError && <p className="text-sm text-muted">{t("common.loading")}</p>}
      {detail && (
        <>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="min-w-0 break-words text-base font-semibold text-ink" data-testid="approval-detail-title">
                {detail.title}
              </h2>
              <ApprovalStatusBadge status={detail.status} />
            </div>
            <p className="mt-1 text-xs text-muted">{t("approvals.agent.requestedBy", { name: detail.requester_label })}</p>
            {detail.ticket_link && (
              <Link
                to="/agent/tickets/$ticketId"
                params={{ ticketId: String(detail.ticket_id) }}
                className="mt-1 inline-block text-xs text-accent hover:underline"
                data-testid="approval-ticket-link"
              >
                {t("approvals.agent.ticket", { number: detail.ticket_number ?? detail.ticket_id })}
                {detail.ticket_title ? ` · ${detail.ticket_title}` : ""}
              </Link>
            )}
          </div>
          {detail.description && (
            <p className="whitespace-pre-wrap break-words text-sm text-ink" data-testid="approval-description">
              {detail.description}
            </p>
          )}
          {detail.describe_lines.length > 0 && (
            <ul className="space-y-0.5 text-sm text-ink" data-testid="approval-describe-lines">
              {detail.describe_lines.map((line, i) => (
                <li key={i} className="break-words">
                  {line}
                </li>
              ))}
            </ul>
          )}
          {pending && !canVote && (
            <p className="text-sm text-muted" data-testid="approval-detail-blocked">
              {item && !item.can_vote ? t(blockedKey(item.blocked_reason)) : t("approvals.agent.cannotVote")}
            </p>
          )}
          {canVote && row && (
            <div className="space-y-2" data-testid="approval-actions">
              {row.on_behalf_of_label && (
                <p className="text-xs text-muted">{t("approvals.agent.onBehalfOf", { name: row.on_behalf_of_label })}</p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" onClick={() => setDecision("approved")} data-testid="approval-approve">
                  {t("approvals.decision.approve")}
                </Button>
                <Button variant="danger" onClick={() => setDecision("rejected")} data-testid="approval-reject">
                  {t("approvals.decision.reject")}
                </Button>
                {row.can_forward && (
                  <Button onClick={() => setForwarding(true)} data-testid="approval-forward">
                    {t("approvals.forward.submit")}
                  </Button>
                )}
              </div>
            </div>
          )}
          {detail.can_cancel && (
            <div>
              <Button size="sm" onClick={() => setConfirmCancel(true)} data-testid="approval-cancel">
                {t("approvals.withdraw.action")}
              </Button>
            </div>
          )}
          <ApprovalSteps detail={detail} />
          <ApprovalAuditList events={detail.events} />
          <WithdrawConfirm
            open={confirmCancel}
            title={detail.title}
            pending={cancel.isPending}
            error={cancel.error}
            onConfirm={() => cancel.mutate()}
            onCancel={() => {
              cancel.reset();
              setConfirmCancel(false);
            }}
          />
          {row && (
            <>
              <DecisionDialog
                open={decision !== null}
                onClose={() => setDecision(null)}
                approvalId={id}
                approverId={row.approver_id}
                initialDecision={decision ?? "approved"}
                onDone={changed}
              />
              <ForwardDialog
                open={forwarding}
                onClose={() => setForwarding(false)}
                approvalId={id}
                approverId={row.approver_id}
                onDone={changed}
              />
            </>
          )}
        </>
      )}
    </div>
  );

  if (phone) {
    // The shared dialog owns focus (moved in, Tab trapped, returned to the row on close) and Escape. While
    // a decision/forward/withdraw dialog sits on top, Escape must only close that one.
    return (
      <Dialog
        open
        onClose={onClose}
        title={t("approvals.agent.detail")}
        closeLabel={t("approvals.agent.back")}
        placement="right"
        size="2xl"
        dismissible={decision === null && !forwarding && !confirmCancel}
      >
        {/* The detail loads after the sheet opens: until it has controls, focus rests on this wrapper (data-autofocus). */}
        <div tabIndex={-1} data-autofocus className="outline-none" data-testid="approval-sheet">
          {body}
        </div>
      </Dialog>
    );
  }
  return (
    <aside
      className="min-w-0 rounded-md border border-hairline bg-surface p-4 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto"
      aria-label={t("approvals.agent.detail")}
      data-testid="approval-panel"
    >
      {body}
    </aside>
  );
}

export function ApprovalsPage() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/agent/approvals" }) as ApprovalsSearch;
  const navigate = useNavigate();
  const phone = usePhoneWidth();
  const tab: ApprovalsTab = search.tab ?? "open";
  const [page, setPage] = useState(0);
  const [approverId, setApproverId] = useState<number | null>(null);

  useEffect(() => setPage(0), [tab]);

  const go = (next: ApprovalsSearch) => void navigate({ to: "/agent/approvals", search: next });
  const state = tab === "done" ? "decided" : "pending";
  const list = useQuery({
    queryKey: approvalsKeys.inbox(state, page),
    queryFn: ({ signal }) =>
      approvalsApi.inbox({ state, limit: APPROVALS_PAGE_SIZE, offset: page * APPROVALS_PAGE_SIZE }, signal),
    enabled: tab !== "delegation",
  });
  const count = useApprovalsCount();

  const items = list.data?.items ?? [];
  const shown = tab === "open" ? [...items].sort(byDeadline) : items;
  const total = list.data?.total ?? 0;
  const selectedItem =
    search.id === undefined
      ? undefined
      : (items.find((i) => i.approval_id === search.id && i.approver_id === approverId) ??
        items.find((i) => i.approval_id === search.id && i.can_vote) ??
        items.find((i) => i.approval_id === search.id));

  const panel = search.id !== undefined && tab !== "delegation";
  return (
    <div className="space-y-4 p-4 sm:p-6" data-testid="approvals-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("approvals.agent.title")}</h1>
      <Tabs
        ariaLabel={t("approvals.agent.title")}
        value={tab}
        onChange={(id) => go({ tab: id as ApprovalsTab })}
        items={[
          { id: "open", label: t("approvals.agent.tabs.open"), count: count.data?.pending },
          { id: "done", label: t("approvals.agent.tabs.done") },
          { id: "delegation", label: t("approvals.agent.tabs.delegation") },
        ]}
      />
      {tab === "delegation" ? (
        <DelegationCard />
      ) : (
        <div className={cn("grid gap-4", panel && !phone && "lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]")}>
          <div className="min-w-0 space-y-3">
            {list.isError ? (
              <p className="text-sm text-danger" role="alert" data-testid="approvals-load-error">
                {t("approvals.agent.loadFailed")}
              </p>
            ) : list.isPending ? (
              <p className="text-sm text-muted">{t("common.loading")}</p>
            ) : shown.length === 0 ? (
              <p className="text-sm text-muted" data-testid="approvals-empty">
                {tab === "open" ? t("approvals.agent.empty") : t("approvals.agent.emptyDone")}
              </p>
            ) : (
              <ul className="space-y-2" data-testid="approvals-list">
                {shown.map((item) => (
                  <InboxRow
                    key={`${item.approval_id}-${item.approver_id}`}
                    item={item}
                    selected={item.approval_id === search.id && item.approver_id === selectedItem?.approver_id}
                    onSelect={() => {
                      setApproverId(item.approver_id);
                      go({ tab: search.tab, id: item.approval_id });
                    }}
                  />
                ))}
              </ul>
            )}
            {total > APPROVALS_PAGE_SIZE && (
              <div className="flex items-center justify-between text-xs text-muted" data-testid="approvals-pager">
                <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)} data-testid="approvals-prev">
                  {t("approvals.agent.prev")}
                </Button>
                <span>
                  {t("approvals.agent.pageOf", { page: page + 1, pages: Math.ceil(total / APPROVALS_PAGE_SIZE) })}
                </span>
                <Button
                  size="sm"
                  disabled={(page + 1) * APPROVALS_PAGE_SIZE >= total}
                  onClick={() => setPage(page + 1)}
                  data-testid="approvals-next"
                >
                  {t("approvals.agent.next")}
                </Button>
              </div>
            )}
          </div>
          {panel && search.id !== undefined && (
            <DetailPanel id={search.id} item={selectedItem} phone={phone} onClose={() => go({ tab: search.tab })} />
          )}
        </div>
      )}
    </div>
  );
}
