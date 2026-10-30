import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, within } from "@testing-library/react";
import i18n from "@/i18n";
import type { ChangeConflict } from "@/lib/changeApi";
import { renderWithClient } from "./changeTestSupport";
import { ConflictList } from "./ConflictList";

const { conflicts } = vi.hoisted(() => ({ conflicts: vi.fn() }));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, conflicts } };
});

const conflict = (over: Partial<ChangeConflict>): ChangeConflict => ({
  kind: "freeze",
  severity: "block",
  ref: "freeze:1",
  hidden: false,
  start: "2026-10-13T00:00:00Z",
  end: "2026-10-16T00:00:00Z",
  shared_services: [1],
  shared_cis: [],
  shared_service_count: 1,
  shared_ci_count: 0,
  ...over,
});

describe("ConflictList", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    conflicts.mockReset();
  });
  afterEach(cleanup);

  it("lists each conflict with its kind, severity, window and what is shared", async () => {
    conflicts.mockResolvedValue([
      conflict({}),
      conflict({ kind: "change_overlap", severity: "warn", ref: "change:133", shared_services: [], shared_service_count: 2, shared_ci_count: 1, shared_cis: [201] }),
      conflict({ kind: "task_outside", severity: "warn", ref: "task:142", shared_service_count: 0, shared_services: [] }),
    ]);
    renderWithClient(<ConflictList ticketId={130} />);
    const rows = await screen.findAllByTestId("change-conflict");
    expect(rows.map((r) => r.getAttribute("data-severity"))).toEqual(["block", "warn", "warn"]);
    expect(rows[0]).toHaveTextContent("Sperrzeitraum");
    expect(rows[0]).toHaveTextContent("Blockiert");
    expect(rows[0]).toHaveTextContent("2026-10-13 00:00 – 2026-10-16 00:00");
    expect(rows[0]).toHaveTextContent("1 gemeinsamer Service");
    expect(within(rows[0]).queryByRole("link")).toBeNull();
    expect(within(rows[1]).getByRole("link")).toHaveAttribute("href", "/agent/itsm/changes/133");
    expect(rows[1]).toHaveTextContent("2 gemeinsame Services, 1 gemeinsames CI");
    expect(within(rows[2]).getByRole("link")).toHaveAttribute("href", "/agent/tickets/142");
  });

  it("shows a conflict with a change the agent cannot read without naming it", async () => {
    conflicts.mockResolvedValue([conflict({ kind: "change_overlap", severity: "warn", ref: null, hidden: true })]);
    renderWithClient(<ConflictList ticketId={130} />);
    const row = await screen.findByTestId("change-conflict");
    expect(row).toHaveTextContent("Überschneidung mit einem Change ohne Leserecht");
    expect(within(row).queryByRole("link")).toBeNull();
  });

  it("says when there is no conflict", async () => {
    conflicts.mockResolvedValue([]);
    renderWithClient(<ConflictList ticketId={130} />);
    expect(await screen.findByTestId("change-conflicts-empty")).toHaveTextContent("Keine Konflikte");
  });
});
