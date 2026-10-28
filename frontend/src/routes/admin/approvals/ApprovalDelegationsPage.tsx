import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Switch } from "@/components/ui/Switch";
import { delegationCreateMessage, periodProblem, toIso } from "@/components/approvals/delegationForm";
import { DelegationRow } from "@/components/approvals/DelegationRow";
import { AgentSelect, inputCls } from "@/components/approvals/pickers";
import { approvalsAdminApi, approvalsAdminKeys } from "@/lib/approvalsAdminApi";
import { AdminApprovalsHeader } from "./AdminApprovalsHeader";

const PAGE_SIZE = 50;

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
  const problem = periodProblem(fromIso, untilIso);

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
  const createMessage = delegationCreateMessage(create.error, untilIso, t);

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
          {rows.map((d) => {
            const state =
              d.valid_until && new Date(d.valid_until).getTime() <= now
                ? "ended"
                : new Date(d.valid_from).getTime() <= now
                  ? "running"
                  : "planned";
            return (
              <DelegationRow
                key={d.id}
                d={d}
                title={t("approvals.admin.delegations.person", { from: d.user_label, to: d.substitute_label })}
                titleTestId="admin-delegation-people"
                stateLabel={t(`approvals.admin.delegations.state.${state}`)}
                prefix="admin-delegation"
                revokeFn={(id) => approvalsAdminApi.deleteDelegation(id)}
                disabled={state === "ended"}
                onChanged={refresh}
              />
            );
          })}
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
