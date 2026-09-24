import { describe, it, expect, beforeEach } from "vitest";
import {
  getTicketNavContext,
  navContextFor,
  neighbours,
  setTicketNavContext,
} from "./ticketNavContext";

describe("ticketNavContext", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("round-trips the context through sessionStorage", () => {
    setTicketNavContext({ label: "cn-nord", to: "/agent/queues", search: { queue_id: 3 }, ids: [5, 7, 9] });
    expect(getTicketNavContext()).toEqual({
      label: "cn-nord",
      to: "/agent/queues",
      search: { queue_id: 3 },
      ids: [5, 7, 9],
    });
  });

  it("only applies to tickets that are part of the stored list", () => {
    setTicketNavContext({ label: "Meine Tickets", to: "/agent/queues", ids: [5, 7] });
    expect(navContextFor(7)?.label).toBe("Meine Tickets");
    expect(navContextFor(8)).toBeNull();
  });

  it("returns null for missing or corrupt storage", () => {
    expect(getTicketNavContext()).toBeNull();
    window.sessionStorage.setItem("tiqora.ticketNavContext", "{not json");
    expect(getTicketNavContext()).toBeNull();
  });

  it("finds previous and next ticket, null at the ends", () => {
    const ctx = { label: "x", to: "/agent/queues", ids: [5, 7, 9] };
    expect(neighbours(ctx, 5)).toEqual({ index: 0, prev: null, next: 7 });
    expect(neighbours(ctx, 7)).toEqual({ index: 1, prev: 5, next: 9 });
    expect(neighbours(ctx, 9)).toEqual({ index: 2, prev: 7, next: null });
  });
});
