/** The tabs of `/admin/itsm/change` and its `?tab=` search value. */
export const CHANGE_ADMIN_TABS = ["templates", "cab", "risk", "freeze", "archive"] as const;
export type ChangeAdminTab = (typeof CHANGE_ADMIN_TABS)[number];

/** The `?tab=` of the page; the router JSON-parses search values, so anything else is the first tab. */
export function parseChangeAdminTab(raw: unknown): ChangeAdminTab {
  return CHANGE_ADMIN_TABS.find((tab) => tab === raw) ?? "templates";
}
