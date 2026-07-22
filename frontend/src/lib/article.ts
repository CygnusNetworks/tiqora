import type { ArticleListItem } from "@/lib/api";

/** Sort key: incoming_time (epoch seconds) preferred, else create_time. Shared
 * by ArticleTimeline (day grouping), ArticleMasterDetail (list ordering) and
 * TicketHeaderActions (picking the latest article for the header reply). */
export function articleSortKey(a: ArticleListItem): number {
  if (typeof a.incoming_time === "number" && a.incoming_time > 0) {
    return a.incoming_time * 1000;
  }
  return new Date(a.create_time).getTime();
}

/** Locale-formatted calendar-day label, used to group articles into day
 * sections in both the split list and the conversation view. */
export function dayKey(iso: string, locale: string): string {
  const d = new Date(iso);
  return new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(d);
}

/** Group a list (already in display order) into contiguous day sections. */
export function groupByDay<T extends { create_time: string }>(
  items: T[],
  locale: string,
): { day: string; items: T[] }[] {
  const groups: { day: string; items: T[] }[] = [];
  for (const item of items) {
    const day = dayKey(item.create_time, locale);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.items.push(item);
    else groups.push({ day, items: [item] });
  }
  return groups;
}
