import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { useOptionalAuth } from "@/auth/AuthContext";
import { formatDateTime } from "@/lib/format";
import {
  approvalErrorCode,
  approvalErrorMessage,
  approvalsApi,
  approvalsKeys,
  type ApprovalDelegation,
} from "@/lib/approvalsApi";
import { AgentSelect, inputCls } from "./pickers";

/** `<input type="datetime-local">` value to an ISO instant, or null when empty/invalid. */
function toIso(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

type Problem = "untilPast" | "untilBeforeFrom";

function DelegationRow({ d, now, onChanged }: { d: ApprovalDelegation; now: number; onChanged: () => void }) {
  const { t, i18n } = useTranslation();
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revoke = useMutation({
    mutationFn: () => approvalsApi.deleteDelegation(d.id),
    onSuccess: () => onChanged(),
    onError: (err) => {
      if (approvalErrorCode(err) === "delegation_not_active") {
        setEnded(true);
        setError(null);
        onChanged();
      } else {
        setError(approvalErrorMessage(err, t));
      }
    },
  });
  const running = new Date(d.valid_from).getTime() <= now;
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2" data-testid="delegation-row" data-delegation-id={d.id}>
      <div className="min-w-0">
        <p className="text-sm font-medium text-ink" data-testid="delegation-substitute">
          {d.substitute_label}
        </p>
        <p className="text-xs text-muted" data-testid="delegation-period">
          {formatDateTime(d.valid_from, i18n.language)}
          {" – "}
          {d.valid_until ? formatDateTime(d.valid_until, i18n.language) : t("approvals.agent.delegation.noEnd")}
          {" · "}
          {running ? t("approvals.agent.delegation.running") : t("approvals.agent.delegation.planned")}
        </p>
        {ended && (
          <p className="text-xs text-muted" role="status" data-testid="delegation-ended">
            {t("approvals.agent.delegation.alreadyEnded")}
          </p>
        )}
        {error && (
          <p className="text-xs text-danger" role="alert" data-testid="delegation-row-error">
            {error}
          </p>
        )}
      </div>
      <Button size="sm" disabled={revoke.isPending || ended} onClick={() => revoke.mutate()} data-testid="delegation-revoke">
        {t("approvals.agent.delegation.revoke")}
      </Button>
    </li>
  );
}

/** "Vertretung": the caller's running and planned substitutes, a form to name a new one and a button to end one. */
export function DelegationCard() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const me = useOptionalAuth()?.user?.id;
  const [substitute, setSubstitute] = useState<number | null>(null);
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [touched, setTouched] = useState(false);
  const [now] = useState(() => Date.now());

  const list = useQuery({
    queryKey: approvalsKeys.delegations(),
    queryFn: ({ signal }) => approvalsApi.delegations(signal),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: approvalsKeys.delegations() });

  const fromIso = toIso(from);
  const untilIso = toIso(until);
  let problem: Problem | null = null;
  if (untilIso) {
    if (new Date(untilIso).getTime() <= Date.now()) problem = "untilPast";
    else if (fromIso && new Date(untilIso) <= new Date(fromIso)) problem = "untilBeforeFrom";
  }

  const create = useMutation({
    mutationFn: () =>
      approvalsApi.createDelegation({ substitute_user_id: substitute as number, valid_from: fromIso, valid_until: untilIso }),
    onSuccess: () => {
      setSubstitute(null);
      setFrom("");
      setUntil("");
      setTouched(false);
      void refresh();
    },
  });

  const submit = () => {
    setTouched(true);
    if (substitute === null || problem || create.isPending) return;
    create.mutate();
  };

  const createError = create.error;
  // A period that ended already is answered 422 `invalid_definition`; it can only come from valid_until.
  const createMessage = !createError
    ? null
    : approvalErrorCode(createError) === "invalid_definition" && untilIso
      ? t("approvals.agent.delegation.untilPast")
      : approvalErrorMessage(createError, t);

  const rows = list.data ?? [];
  return (
    <section className="space-y-4" data-testid="delegation-card">
      <div>
        <h2 className="text-sm font-semibold text-ink">{t("approvals.agent.delegation.title")}</h2>
        <p className="mt-1 text-xs text-muted">{t("approvals.agent.delegation.hint")}</p>
      </div>

      {list.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="delegation-load-error">
          {t("approvals.agent.loadFailed")}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted" data-testid="delegation-empty">
          {list.isPending ? t("common.loading") : t("approvals.agent.delegation.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline rounded-md border border-hairline bg-surface px-3" data-testid="delegation-list">
          {rows.map((d) => (
            <DelegationRow key={d.id} d={d} now={now} onChanged={() => void refresh()} />
          ))}
        </ul>
      )}

      <form
        className="space-y-3 rounded-md border border-hairline bg-surface p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        data-testid="delegation-form"
      >
        <h3 className="text-sm font-semibold text-ink">{t("approvals.agent.delegation.new")}</h3>
        <label className="block space-y-1 text-xs text-muted">
          <span>{t("approvals.agent.delegation.substitute")}</span>
          <AgentSelect
            value={substitute}
            onChange={setSubstitute}
            excludeId={me}
            testId="delegation-substitute-select"
            ariaLabel={t("approvals.agent.delegation.substitute")}
          />
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs text-muted">
            <span>{t("approvals.agent.delegation.from")}</span>
            <input
              type="datetime-local"
              className={inputCls}
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              data-testid="delegation-from"
            />
          </label>
          <label className="block space-y-1 text-xs text-muted">
            <span>{t("approvals.agent.delegation.until")}</span>
            <input
              type="datetime-local"
              className={inputCls}
              value={until}
              onChange={(e) => setUntil(e.target.value)}
              aria-invalid={problem !== null}
              data-testid="delegation-until"
            />
          </label>
        </div>
        <p className="text-xs text-muted">{t("approvals.agent.delegation.periodHint")}</p>
        {touched && substitute === null && (
          <p className="text-xs text-danger" role="alert" data-testid="delegation-substitute-error">
            {t("approvals.agent.delegation.substituteMissing")}
          </p>
        )}
        {problem && (
          <p className="text-xs text-danger" role="alert" data-testid="delegation-period-error">
            {t(`approvals.agent.delegation.${problem}`)}
          </p>
        )}
        {createMessage && (
          <p className="text-xs text-danger" role="alert" data-testid="delegation-create-error">
            {createMessage}
          </p>
        )}
        <div className="flex justify-end">
          <Button variant="primary" type="submit" disabled={create.isPending} data-testid="delegation-create">
            {t("approvals.agent.delegation.create")}
          </Button>
        </div>
      </form>
    </section>
  );
}
