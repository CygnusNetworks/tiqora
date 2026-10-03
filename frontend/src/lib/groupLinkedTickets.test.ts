import { describe, it, expect } from "vitest";
import type { TicketListItem } from "@/lib/api";
import { groupLinkedTickets } from "./groupLinkedTickets";

function tk(id: number, extra: Partial<TicketListItem> = {}): TicketListItem {
  return { id, tn: `T${id}`, links: [], ...extra } as unknown as TicketListItem;
}
const link = (id: number, role: string | null = null) => ({
  ticket_id: id,
  tn: `T${id}`,
  link_type: role ? "ParentChild" : "Normal",
  role,
});

describe("groupLinkedTickets", () => {
  it("keeps unlinked tickets in place", () => {
    const out = groupLinkedTickets([tk(1), tk(2), tk(3)]);
    expect(out.map((o) => [o.ticket.id, o.child])).toEqual([
      [1, false],
      [2, false],
      [3, false],
    ]);
  });

  it("puts a child under its parent even when the child sorts first", () => {
    const out = groupLinkedTickets([
      tk(5, { links: [link(9, "parent")] }),
      tk(7),
      tk(9, { links: [link(5, "child"), link(6, "child")] }),
      tk(6, { links: [link(9, "parent")] }),
    ]);
    expect(out.map((o) => [o.ticket.id, o.child, o.midChild])).toEqual([
      [9, false, false],
      [5, true, true],
      [6, true, false],
      [7, false, false],
    ]);
  });

  it("hangs a merged ticket under its main ticket and links plain pairs", () => {
    const out = groupLinkedTickets([
      tk(1, { links: [link(3)] }),
      tk(2, { merged_into_id: 3 }),
      tk(3, { links: [link(1)] }),
    ]);
    expect(out.map((o) => [o.ticket.id, o.child])).toEqual([
      [1, false],
      [3, true],
      [2, false],
    ]);
  });
});
