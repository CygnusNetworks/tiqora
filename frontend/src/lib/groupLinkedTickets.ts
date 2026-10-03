import type { TicketListItem } from "@/lib/api";

/**
 * Re-order one page so linked tickets that are on it sit together: the
 * parent (or, for plain links, whichever comes first) leads, its linked and
 * merged-in partners follow as children. Tickets without a partner on the
 * page keep their position. Grouping is per page — a partner on another page
 * stays where the sort puts it (the chip still names it).
 */
export function groupLinkedTickets(
  items: TicketListItem[],
): { ticket: TicketListItem; child: boolean; midChild: boolean }[] {
  const byId = new Map(items.map((it) => [it.id, it]));
  const placed = new Set<number>();
  const out: { ticket: TicketListItem; child: boolean; midChild: boolean }[] = [];
  const partnersOnPage = (it: TicketListItem): TicketListItem[] => {
    const ids = (it.links ?? []).map((l) => l.ticket_id);
    // A merged ticket hangs under its main ticket; the main ticket pulls it in.
    for (const other of items) if (other.merged_into_id === it.id) ids.push(other.id);
    return ids.map((id) => byId.get(id)).filter((x): x is TicketListItem => !!x && x.id !== it.id);
  };
  const place = (it: TicketListItem) => {
    if (placed.has(it.id)) return;
    // A child waits for its parent / main ticket so it lands under it.
    const lead = (it.links ?? []).find((l) => l.role === "parent" && byId.has(l.ticket_id));
    const mainOnPage = it.merged_into_id != null && byId.has(it.merged_into_id);
    if (lead && !placed.has(lead.ticket_id)) return place(byId.get(lead.ticket_id)!);
    if (mainOnPage && !placed.has(it.merged_into_id!)) return place(byId.get(it.merged_into_id!)!);
    placed.add(it.id);
    out.push({ ticket: it, child: false, midChild: false });
    const kids = partnersOnPage(it).filter((k) => !placed.has(k.id));
    kids.forEach((k, i) => {
      placed.add(k.id);
      out.push({ ticket: k, child: true, midChild: i < kids.length - 1 });
    });
  };
  items.forEach(place);
  return out;
}
