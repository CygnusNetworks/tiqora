/** Tabs and `?tab=` handling of the CI detail page. */
export const CI_DETAIL_TABS = ["overview", "links", "dependencies", "tickets", "versions", "history"] as const;
export type CiDetailTab = (typeof CI_DETAIL_TABS)[number];
export type CiDetailSearch = { tab?: CiDetailTab };

/** `?tab=` of the CI page; anything unknown (or `overview`, the default) is dropped. */
export function validateCiDetailSearch(s: Record<string, unknown>): CiDetailSearch {
  const tab = s.tab;
  return typeof tab === "string" && tab !== "overview" && (CI_DETAIL_TABS as readonly string[]).includes(tab) ? { tab: tab as CiDetailTab } : {};
}
