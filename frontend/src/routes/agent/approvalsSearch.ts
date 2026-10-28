import type { ApprovalsTab } from "@/lib/approvalsApi";

export type ApprovalsSearch = { id?: number; tab?: ApprovalsTab };

const TABS: readonly ApprovalsTab[] = ["open", "done", "delegation"];

/** Search params of `/agent/approvals`: `id` deep-links the side panel, `tab` picks the list. */
export function parseApprovalsSearch(s: Record<string, unknown>): ApprovalsSearch {
  const id = typeof s.id === "number" ? s.id : typeof s.id === "string" && /^\d+$/.test(s.id) ? Number(s.id) : undefined;
  return {
    id: id !== undefined && Number.isInteger(id) && id > 0 ? id : undefined,
    tab: TABS.includes(s.tab as ApprovalsTab) ? (s.tab as ApprovalsTab) : undefined,
  };
}
