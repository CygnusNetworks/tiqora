import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { SelectField } from "@/components/ui/SelectField";
import { api } from "@/lib/api";
import { reportsApi } from "@/lib/reportsApi";

export const inputCls =
  "w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";

/** Agents for a picker: every valid agent (`GET /reference/agents`), not the queue-scoped owner list. */
export function AgentSelect({
  value,
  onChange,
  testId,
  ariaLabel,
  excludeId,
}: {
  value: number | null;
  onChange: (id: number) => void;
  /** An agent left out of the list (e.g. the current user). */
  excludeId?: number;
  testId: string;
  ariaLabel: string;
}) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: ({ signal }) => api.listReferenceAgents(signal),
    staleTime: 60_000,
  });
  const items = (q.data ?? [])
    .filter((a) => a.id !== excludeId)
    .map((a) => ({ value: a.id, label: a.full_name || a.login }));
  return (
    <SelectField
      items={items}
      value={value}
      onChange={onChange}
      testId={testId}
      aria-label={ariaLabel}
      placeholder={t("approvals.picker.agent")}
    />
  );
}

/** Groups an agent may name (`GET /reference/share-groups`). */
export function GroupSelect({
  value,
  onChange,
  testId,
  ariaLabel,
}: {
  value: string;
  onChange: (id: string) => void;
  testId: string;
  ariaLabel: string;
}) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["reports", "share-groups"],
    queryFn: ({ signal }) => reportsApi.shareGroups(signal),
    staleTime: 60_000,
  });
  const items = (q.data ?? []).map((g) => ({ value: String(g.id), label: g.name }));
  return (
    <SelectField
      items={items}
      value={value || null}
      onChange={onChange}
      testId={testId}
      aria-label={ariaLabel}
      placeholder={t("approvals.picker.group")}
    />
  );
}

/** Valid roles (`GET /reference/roles`); a stored role that no longer exists stays visible and says so. */
export function RoleSelect({
  value,
  onChange,
  testId,
  ariaLabel,
}: {
  value: string;
  onChange: (id: string) => void;
  testId: string;
  ariaLabel: string;
}) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["reference", "roles"],
    queryFn: ({ signal }) => api.listReferenceRoles(signal),
    staleTime: 60_000,
  });
  if (q.isError) {
    return (
      <p className="py-1.5 text-xs text-danger" role="alert" data-testid={`${testId}-error`}>
        {t("approvals.picker.rolesFailed")}
      </p>
    );
  }
  const items = (q.data ?? []).map((r) => ({ value: String(r.id), label: r.name }));
  if (q.isSuccess && value && !items.some((i) => i.value === value)) {
    items.unshift({ value, label: t("approvals.picker.roleGone", { id: value }) });
  }
  return (
    <SelectField
      items={items}
      value={value || null}
      onChange={onChange}
      testId={testId}
      aria-label={ariaLabel}
      placeholder={t("approvals.picker.role")}
    />
  );
}

/** Customer user typeahead (`GET /reference/customers`); the chosen value is the customer login. */
export function CustomerSelect({
  value,
  onChange,
  testId,
  ariaLabel,
}: {
  value: string;
  onChange: (login: string) => void;
  testId: string;
  ariaLabel: string;
}) {
  const { t } = useTranslation();
  const [term, setTerm] = useState("");
  const q = useQuery({
    queryKey: ["reference", "customers", term],
    queryFn: ({ signal }) => api.searchReferenceCustomers({ q: term, limit: 20 }, signal),
    enabled: term.trim().length >= 2,
    staleTime: 30_000,
  });
  const found = (q.data ?? []).map((c) => ({
    value: c.login,
    label: c.full_name ? `${c.full_name} (${c.login})` : c.login,
  }));
  const items = value && !found.some((i) => i.value === value) ? [{ value, label: value }, ...found] : found;
  return (
    <div className="flex min-w-0 flex-col gap-1 sm:flex-row" data-testid={`${testId}-wrap`}>
      <input
        className={inputCls}
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        aria-label={t("approvals.picker.customerSearch")}
        placeholder={t("approvals.picker.customerSearch")}
        data-testid={`${testId}-search`}
      />
      <SelectField
        items={items}
        value={value || null}
        onChange={onChange}
        testId={testId}
        aria-label={ariaLabel}
        placeholder={t("approvals.picker.customer")}
      />
    </div>
  );
}
