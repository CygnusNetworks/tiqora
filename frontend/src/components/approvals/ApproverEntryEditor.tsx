import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { Schemas } from "@tiqora/api-client";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { approvalsApi, approvalsKeys } from "@/lib/approvalsApi";
import { AgentSelect, CustomerSelect, GroupSelect, RoleSelect } from "./pickers";

export type ApproverEntry = Schemas["ApproverEntry"];
export type ApproverKind = ApproverEntry["kind"];

const KINDS: ApproverKind[] = ["user", "group", "role", "list", "customer_user", "resolver"];
/** The API accepts at most this many approver entries per step. */
export const MAX_APPROVER_ENTRIES = 50;

/**
 * Rows of "who approves": a kind select plus the picker that fits the kind. Refs are ids as text
 * (agent, group, role, Freigabegruppe), the login (customer user) or the resolver key. A row without
 * a ref is kept while it is being filled in; the owner of the value validates. `allowCustomers`
 * gates the customer user kind and resolvers that yield customers.
 */
export function ApproverEntryEditor({
  value,
  onChange,
  subjectType,
  allowCustomers,
}: {
  value: ApproverEntry[];
  onChange: (next: ApproverEntry[]) => void;
  subjectType?: string | null;
  allowCustomers: boolean;
}) {
  const { t } = useTranslation();
  const listsQ = useQuery({
    queryKey: approvalsKeys.lists(),
    queryFn: ({ signal }) => approvalsApi.lists(signal),
    staleTime: 60_000,
  });
  const resolversQ = useQuery({
    queryKey: approvalsKeys.resolvers(subjectType),
    queryFn: ({ signal }) => approvalsApi.resolvers(subjectType, signal),
    staleTime: 60_000,
  });

  const setRow = (i: number, entry: ApproverEntry) => onChange(value.map((e, idx) => (idx === i ? entry : e)));
  const kindItems = (current: ApproverKind) =>
    KINDS.filter((k) => k !== "customer_user" || allowCustomers || current === "customer_user").map((k) => ({
      value: k,
      label: t(`approvals.entry.kind.${k}`),
    }));
  const resolverItems = (resolversQ.data ?? [])
    .filter((r) => allowCustomers || !r.yields_customers)
    .map((r) => ({ value: r.key, label: t(r.title_key, { defaultValue: r.key }) }));

  const picker = (entry: ApproverEntry, i: number) => {
    const testId = `entry-ref-${i}`;
    const ariaLabel = t(`approvals.entry.kind.${entry.kind}`);
    const set = (ref: string) => setRow(i, { kind: entry.kind, ref });
    switch (entry.kind) {
      case "user":
        return (
          <AgentSelect
            value={entry.ref ? Number(entry.ref) : null}
            onChange={(id) => set(String(id))}
            testId={testId}
            ariaLabel={ariaLabel}
          />
        );
      case "group":
        return <GroupSelect value={entry.ref} onChange={set} testId={testId} ariaLabel={ariaLabel} />;
      case "role":
        return <RoleSelect value={entry.ref} onChange={set} testId={testId} ariaLabel={ariaLabel} />;
      case "list":
        return (
          <SelectField
            items={(listsQ.data ?? []).map((l) => ({
              value: String(l.id),
              label: l.name,
              hint: t("approvals.entry.members", { count: l.member_count }),
            }))}
            value={entry.ref || null}
            onChange={set}
            testId={testId}
            aria-label={ariaLabel}
            placeholder={t("approvals.picker.list")}
          />
        );
      case "customer_user":
        return <CustomerSelect value={entry.ref} onChange={set} testId={testId} ariaLabel={ariaLabel} />;
      case "resolver":
        return (
          <SelectField
            items={resolverItems}
            value={entry.ref || null}
            onChange={set}
            testId={testId}
            aria-label={ariaLabel}
            placeholder={t("approvals.picker.resolver")}
          />
        );
    }
  };

  return (
    <div className="space-y-2" data-testid="entry-editor">
      {value.map((entry, i) => (
        <div key={i} className="flex flex-col gap-2 sm:flex-row sm:items-start" data-testid="entry-row">
          <div className="sm:w-48 sm:shrink-0">
            <SelectField
              items={kindItems(entry.kind)}
              value={entry.kind}
              onChange={(kind) => kind !== entry.kind && setRow(i, { kind, ref: "" })}
              testId={`entry-kind-${i}`}
              aria-label={t("approvals.entry.kindLabel")}
            />
          </div>
          <div className="min-w-0 flex-1">{picker(entry, i)}</div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onChange(value.filter((_, idx) => idx !== i))}
            aria-label={t("approvals.entry.remove")}
            data-testid={`entry-remove-${i}`}
          >
            ✕
          </Button>
        </div>
      ))}
      <Button
        size="sm"
        onClick={() => onChange([...value, { kind: "user", ref: "" }])}
        disabled={value.length >= MAX_APPROVER_ENTRIES}
        data-testid="entry-add"
      >
        {t("approvals.entry.add")}
      </Button>
    </div>
  );
}
