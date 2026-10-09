/**
 * Owner / responsible pickers fed by the ACL- and queue-scoped field options
 * (`POST /reference/field-options`, Znuny `_GetOwners` / `_GetResponsibles`) instead of the
 * unscoped `GET /reference/agents`. The server decides the scope from the context (ticket, bulk
 * selection, queue); without a queue the list is empty, like Znuny.
 */
import { useQuery } from "@tanstack/react-query";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import { fieldOptionsApi, type FieldOptionsContext } from "./fieldOptionsApi";

export type AgentField = "owner" | "responsible";

/** Znuny frontend module whose Action ACLs apply to the picker. */
export const AGENT_FIELD_ACTION: Record<AgentField, string> = {
  owner: "AgentTicketOwner",
  responsible: "AgentTicketResponsible",
};

export function useAgentOptions(
  field: AgentField,
  context: Partial<FieldOptionsContext>,
  { enabled = true }: { enabled?: boolean } = {},
): { items: SelectMenuItem<number>[]; isLoading: boolean } {
  const q = useQuery({
    queryKey: ["reference", "field-options", "agents", field, context],
    queryFn: ({ signal }) =>
      fieldOptionsApi.fetch(
        { fields: [field], context: { action: AGENT_FIELD_ACTION[field], ...context } },
        signal,
      ),
    enabled,
  });
  const items = (q.data?.options[field] ?? []).map((o) => ({ value: Number(o.value), label: o.label }));
  return { items, isLoading: enabled && q.isLoading };
}
