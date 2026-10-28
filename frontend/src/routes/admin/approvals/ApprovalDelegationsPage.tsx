import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { Schemas } from "@tiqora/api-client";
import { Button } from "@/components/ui/Button";
import { Switch } from "@/components/ui/Switch";
import { AgentSelect, inputCls } from "@/components/approvals/pickers";
import { approvalErrorCode, approvalErrorMessage } from "@/lib/approvalsApi";
import { approvalsAdminApi, approvalsAdminKeys } from "@/lib/approvalsAdminApi";
import { formatDateTime } from "@/lib/format";
import { AdminApprovalsHeader } from "./AdminApprovalsHeader";

type Delegation = Schemas["DelegationOut"];

const PAGE_SIZE = 50;

/** `<input type="datetime-local">` value to an ISO instant, or null when empty/invalid. */
function toIso(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function Row({ d, now, onChanged }: { d: Delegation; now: number; onChanged: () => void }) {
  const { t, i18n } = useTranslation();
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revoke = useMutation({
    mutationFn: () => approvalsAdminApi.deleteDelegation(d.id),
    onSuccess: () => onChanged(),
    onError: (err) => {
      // An ended one answers 403 `delegation_not_active`: say so and reload instead of showing a failure.
      if (approvalErrorCode(err) === "delegation_not_active") {
        setEnded(true);
        setError(null);
        onChanged();
      } else {
        setError(approvalErrorMessage(err, t));
      }
    },
  });
  const state =
    d.valid_until && new Date(d.valid_until).getTime() <= now
      ? "ended"
      : new Date(d.valid_from).getTime() <= now
        ? "running"
        : "planned";
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 px-3 py-2" data-testid="admin-delegation-row" data-delegation-id={d.id}>
      <div className="min-w-0">
        <p className="text-sm font-medium text-ink" data-testid="admin-delegation-people">
          {t("approvals.admin.delegations.person", { from: d.user_label, to: d.substitute_label })}
        </p>
        <p className="text-xs text-muted" data-testid="admin-delegation-period">
          {formatDateTime(d.valid_from, i18n.language)}
          {" – "}
          {d.valid_until ? formatDateTime(d.valid_until, i18n.language) : t("approvals.agent.delegation.noEnd")}
          {" · "}
          {t(`approvals.admin.delegations.state.${state}`)}
        </p>
        {ended && (
          <p className="text-xs text-muted" role="status" data-testid="admin-delegation-ended">
            {t("approvals.agent.delegation.alreadyEnded")}
          </p>
        )}
        {error && (
          <p className="text-xs text-danger" role="alert" data-testid="admin-delegation-row-error">
            {error}
          </p>
        )}
      </div>
      <Button size="sm" disabled={revoke.isPending || ended || state === "ended"} onClick={() => revoke.mutate()} data-testid="admin-delegation-revoke">
        {t("approvals.agent.delegation.revoke")}
      </Button>
    </li>
  );
}

/** Every substitute arrangement of the system; an admin can name one for someone else and end any. */
export function ApprovalDelegationsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [userFilter, setUserFilter] = useState<number | null>(null);
  const [includeEnded, setIncludeEnded] = useState(false);
  const [page, setPage] = useState(0);
  const [now] = useState(() => Date.now());

  const [user, setUser] = useState<number | null>(null);
  const [substitute, setSubstitute] = useState<number | null>(null);
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [touched, setTouched] = useState(false);

  const q = useQuery({
    queryKey: [...approvalsAdminKeys.delegations(userFilter, includeEnded), page],
    queryFn: ({ signal }) =>
      approvalsAdminApi.delegations(
        { user_id: userFilter ?? undefined, include_ended: includeEnded || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE },
        signal,
      ),
  });
  const rows = q.data ?? [];
  const refresh = () => void qc.invalidateQueries({ queryKey: ["admin", "approvals", "delegations"] });

  const fromIso = toIso(from);
  const untilIso = toIso(until);
  let problem: "untilPast" | "untilBeforeFrom" | null = null;
  if (untilIso) {
    if (new Date(untilIso).getTime() <= Date.now()) problem = "untilPast";
    else if (fromIso && new Date(untilIso) <= new Date(fromIso)) problem = "untilBeforeFrom";
  }

  const create = useMutation({
    mutationFn: () =>
      approvalsAdminApi.createDelegation({
        user_id: user as number,
        substitute_user_id: substitute as number,
        valid_from: fromIso,
        valid_until: untilIso,
      }),
    onSuccess: () => {
      setUser(null);
      setSubstitute(null);
      setFrom("");
      setUntil("");
      setTouched(false);
      refresh();
    },
  });
  const submit = () => {
    setTouched(true);
    if (user === null || substitute === null || problem || create.isPending) return;
    create.mutate();
  };
  const createMessage = !create.error
    ? null
    : approvalErrorCode(create.error) === "invalid_definition" && untilIso
      ? t("approvals.agent.delegation.untilPast")
      : approvalErrorMessage(create.error, t);

  return (
    <div className="space-y-4 p-4" data-testid="admin-approval-delegations-page">
      <AdminApprovalsHeader title={t("admin.nav.approvalsDelegations")} />

      <div className="flex flex-wrap items-end gap-4 rounded-md border border-hairline bg-surface p-3">
        <div className="min-w-48 text-xs font-medium text-muted">
          {t("approvals.admin.delegations.filterUser")}
          <div className="mt-1 flex gap-2">
            <AgentSelect
              value={userFilter}
              onChange={(id) => {
                setUserFilter(id);
                setPage(0);
              }}
              testId="admin-delegations-filter-user"
              ariaLabel={t("approvals.admin.delegations.filterUser")}
            />
            {userFilter !== null && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setUserFilter(null);
                  setPage(0);
                }}
                data-testid="admin-delegations-filter-clear"
              >
                {t("approvals.admin.delegations.filterClear")}
              </Button>
            )}
          </div>
        </div>
        <label className="flex items-center gap-2 pb-1.5 text-sm text-ink">
          <Switch
            checked={includeEnded}
            onChange={(v) => {
              setIncludeEnded(v);
              setPage(0);
            }}
            testId="admin-delegations-include-ended"
            aria-label={t("approvals.admin.delegations.includeEnded")}
          />
          {t("approvals.admin.delegations.includeEnded")}
        </label>
      </div>

      {q.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="admin-delegations-error">
          {t("approvals.agent.loadFailed")}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted" data-testid="admin-delegations-empty">
          {q.isPending ? t("common.loading") : t("approvals.admin.delegations.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline rounded-md border border-hairline bg-surface" data-testid="admin-delegations-list">
          {rows.map((d) => (
            <Row key={d.id} d={d} now={now} onChanged={refresh} />
          ))}
        </ul>
      )}

      {(page > 0 || rows.length === PAGE_SIZE) && (
        <div className="flex items-center justify-between text-sm text-muted" data-testid="admin-delegations-paging">
          <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)} data-testid="admin-delegations-prev">
            {t("approvals.agent.prev")}
          </Button>
          <span>{page + 1}</span>
          <Button size="sm" disabled={rows.length < PAGE_SIZE} onClick={() => setPage(page + 1)} data-testid="admin-delegations-next">
            {t("approvals.agent.next")}
          </Button>
        </div>
      )}

      <form
        className="space-y-3 rounded-md border border-hairline bg-surface p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        data-testid="admin-delegation-form"
      >
        <h2 className="text-sm font-semibold text-ink">{t("approvals.admin.delegations.new")}</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="text-xs text-muted">
            {t("approvals.admin.delegations.user")}
            <div className="mt-1">
              <AgentSelect value={user} onChange={setUser} excludeId={substitute ?? undefined} testId="admin-delegation-user" ariaLabel={t("approvals.admin.delegations.user")} />
            </div>
          </div>
          <div className="text-xs text-muted">
            {t("approvals.agent.delegation.substitute")}
            <div className="mt-1">
              <AgentSelect value={substitute} onChange={setSubstitute} excludeId={user ?? undefined} testId="admin-delegation-substitute" ariaLabel={t("approvals.agent.delegation.substitute")} />
            </div>
          </div>
          <label className="block space-y-1 text-xs text-muted">
            <span>{t("approvals.agent.delegation.from")}</span>
            <input type="datetime-local" className={inputCls} value={from} onChange={(e) => setFrom(e.target.value)} data-testid="admin-delegation-from" />
          </label>
          <label className="block space-y-1 text-xs text-muted">
            <span>{t("approvals.agent.delegation.until")}</span>
            <input type="datetime-local" className={inputCls} value={until} onChange={(e) => setUntil(e.target.value)} aria-invalid={problem !== null} data-testid="admin-delegation-until" />
          </label>
        </div>
        <p className="text-xs text-muted">{t("approvals.agent.delegation.periodHint")}</p>
        {touched && (user === null || substitute === null) && (
          <p className="text-xs text-danger" role="alert" data-testid="admin-delegation-missing">
            {t("approvals.admin.delegations.peopleMissing")}
          </p>
        )}
        {problem && (
          <p className="text-xs text-danger" role="alert" data-testid="admin-delegation-period-error">
            {t(`approvals.agent.delegation.${problem}`)}
          </p>
        )}
        {createMessage && (
          <p className="text-xs text-danger" role="alert" data-testid="admin-delegation-create-error">
            {createMessage}
          </p>
        )}
        <div className="flex justify-end">
          <Button variant="primary" type="submit" disabled={create.isPending} data-testid="admin-delegation-create">
            {t("approvals.agent.delegation.create")}
          </Button>
        </div>
      </form>
    </div>
  );
}
