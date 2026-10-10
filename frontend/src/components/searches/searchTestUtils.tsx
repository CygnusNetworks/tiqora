import { fireEvent } from "@testing-library/react";
import type { SavedSearch } from "@/lib/savedSearchesApi";

/** A saved search as `GET /saved-searches` answers it; override what a test cares about. */
export function savedSearch(over: Partial<SavedSearch> = {}): SavedSearch {
  return {
    id: 1,
    name: "Suche",
    criteria: { version: 1 },
    criteria_error: null,
    include_archived: false,
    sort_by: "activity",
    sort_order: "desc",
    owner: { user_id: 1, name: "Alex Turner" },
    is_owner: true,
    can_edit: true,
    group_ids: [],
    show_in_sidebar: true,
    show_count: false,
    position: 0,
    source: "tiqora",
    source_name: null,
    version: 1,
    changed_at: "2026-10-06T10:00:00Z",
    ...over,
  } as SavedSearch;
}

// No user-event in this repo: thin helpers over fireEvent.
export const user = {
  click: async (el: Element) => void fireEvent.click(el),
  type: async (el: Element, text: string) => {
    for (const ch of text) fireEvent.change(el, { target: { value: (el as HTMLInputElement).value + ch } });
  },
};
