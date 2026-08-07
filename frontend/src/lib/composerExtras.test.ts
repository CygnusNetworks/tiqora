import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { postComposerExtras } from "./composerExtras";

const { createTicketMention, createTicketTimeAccounting } = vi.hoisted(() => ({
  createTicketMention: vi.fn(),
  createTicketTimeAccounting: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { createTicketMention, createTicketTimeAccounting } };
});

const ada = { id: 2, name: "Ada Lovelace" };
const bob = { id: 3, name: "Bob Stone" };

function run(body: string, mentions = [ada], timeUnits = "") {
  return postComposerExtras(7, {
    body,
    mentions,
    timeUnits,
    queryClient: new QueryClient(),
  });
}

describe("postComposerExtras", () => {
  beforeEach(() => {
    createTicketMention.mockReset().mockResolvedValue({ id: 1 });
    createTicketTimeAccounting.mockReset().mockResolvedValue({ id: 1 });
  });

  it("records the mentions still named in the body", async () => {
    const res = await run("danke @Ada Lovelace", [ada, bob]);
    expect(createTicketMention).toHaveBeenCalledTimes(1);
    expect(createTicketMention).toHaveBeenCalledWith(7, { user_id: 2 });
    expect(res.failed).toEqual([]);
  });

  it("writes nothing when the name was edited back out", async () => {
    await run("danke", [ada]);
    expect(createTicketMention).not.toHaveBeenCalled();
  });

  it("books the minutes from the footer chip", async () => {
    await run("text", [], "15");
    expect(createTicketTimeAccounting).toHaveBeenCalledWith(7, { time_unit: 15 });
  });

  it("books nothing for a blank, zero or unparseable field", async () => {
    for (const value of ["", "  ", "0", "-3", "abc"]) {
      await run("text", [], value);
    }
    expect(createTicketTimeAccounting).not.toHaveBeenCalled();
  });

  it("names the failing side instead of throwing", async () => {
    createTicketTimeAccounting.mockRejectedValue(new Error("boom"));
    const res = await run("hi @Ada Lovelace", [ada], "5");
    expect(res.failed).toEqual(["time"]);
    // The mention still went through — a retry must not repeat it.
    expect(createTicketMention).toHaveBeenCalledTimes(1);
  });

  it("reports both sides when both fail", async () => {
    createTicketMention.mockRejectedValue(new Error("boom"));
    createTicketTimeAccounting.mockRejectedValue(new Error("boom"));
    const res = await run("hi @Ada Lovelace", [ada], "5");
    expect(res.failed).toEqual(["mentions", "time"]);
  });
});
