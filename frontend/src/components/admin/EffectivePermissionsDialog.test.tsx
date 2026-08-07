import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { UserOut } from "@/lib/api";
import { EffectivePermissionsDialog } from "./EffectivePermissionsDialog";

const { getUserEffectivePermissions } = vi.hoisted(() => ({
  getUserEffectivePermissions: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { getUserEffectivePermissions } };
});

const USER = {
  id: 7,
  login: "adam",
  first_name: "Peter",
  last_name: "Adam",
  title: null,
  valid_id: 1,
  create_time: null,
  change_time: null,
} as UserOut;

/** One valid and one invalid entry in every section. */
const PAYLOAD = {
  roles: [
    { id: 1, name: "Live role", comments: null, valid_id: 1, create_time: null, change_time: null },
    { id: 2, name: "Dead role", comments: null, valid_id: 2, create_time: null, change_time: null },
  ],
  groups: [
    {
      group_id: 10,
      group_name: "Live group",
      valid_id: 1,
      keys: ["ro", "note"],
      sources: [
        { key: "ro", via: "direct", valid_id: 1 },
        { key: "note", via: "Rolle: Dead role", valid_id: 2 },
      ],
    },
    {
      group_id: 11,
      group_name: "Dead group",
      valid_id: 2,
      keys: ["rw"],
      sources: [{ key: "rw", via: "direct", valid_id: 1 }],
    },
  ],
  queues: [
    {
      queue_id: 100,
      queue_name: "Live queue",
      valid_id: 1,
      group_id: 10,
      group_name: "Live group",
      group_valid_id: 1,
      keys: ["ro"],
    },
    {
      queue_id: 101,
      queue_name: "Queue in dead group",
      valid_id: 1,
      group_id: 11,
      group_name: "Dead group",
      group_valid_id: 2,
      keys: ["rw"],
    },
  ],
};

function wrap() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <EffectivePermissionsDialog user={USER} onClose={vi.fn()} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("EffectivePermissionsDialog", () => {
  beforeEach(() => {
    getUserEffectivePermissions.mockReset().mockResolvedValue(PAYLOAD);
  });

  /** Group names also appear in the queue table's "group" column, so every
   * group assertion is scoped to the groups table. */
  const groupsTable = () => within(screen.getByTestId("effective-permissions-groups"));
  const queuesTable = () => within(screen.getByTestId("effective-permissions-queues"));

  it("hides entries tied to invalid resources by default", async () => {
    wrap();
    await screen.findByTestId("effective-permissions-content");

    expect(groupsTable().getByText("Live group")).toBeInTheDocument();
    expect(groupsTable().queryByText("Dead group")).not.toBeInTheDocument();
    expect(screen.getByText("Live role")).toBeInTheDocument();
    expect(screen.queryByText("Dead role")).not.toBeInTheDocument();
    // Valid queue, but its group is invalid — the permission is not in force.
    expect(queuesTable().getByText("Live queue")).toBeInTheDocument();
    expect(queuesTable().queryByText("Queue in dead group")).not.toBeInTheDocument();
  });

  it("shows only the invalid entries when the filter is flipped", async () => {
    wrap();
    await screen.findByTestId("effective-permissions-content");

    fireEvent.click(screen.getByTestId("effective-permissions-valid-invalid"));
    expect(groupsTable().getByText("Dead group")).toBeInTheDocument();
    expect(groupsTable().queryByText("Live group")).not.toBeInTheDocument();
    expect(queuesTable().getByText("Queue in dead group")).toBeInTheDocument();
    expect(queuesTable().queryByText("Live queue")).not.toBeInTheDocument();
  });

  it("shows both once the filter is set to all", async () => {
    wrap();
    await screen.findByTestId("effective-permissions-content");

    fireEvent.click(screen.getByTestId("effective-permissions-valid-all"));
    expect(groupsTable().getByText("Live group")).toBeInTheDocument();
    expect(groupsTable().getByText("Dead group")).toBeInTheDocument();
    expect(screen.getByText("Dead role")).toBeInTheDocument();
  });

  it("strikes through a key that only an invalid role grants", async () => {
    wrap();
    await screen.findByTestId("effective-permissions-content");

    // Scoped to the groups table — the queue table lists the same key names.
    const groups = within(screen.getByTestId("effective-permissions-groups"));
    // "ro" is granted directly and valid; "note" comes from the invalid role.
    expect(groups.getByText("ro").className).not.toContain("line-through");
    expect(groups.getByText("note").className).toContain("line-through");
  });
});
