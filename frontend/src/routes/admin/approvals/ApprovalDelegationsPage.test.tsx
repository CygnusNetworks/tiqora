import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";
import { ApprovalDelegationsPage } from "./ApprovalDelegationsPage";

beforeAll(useGerman);

const D = {
  id: 1,
  user_id: 2,
  user_label: "Alex Turner",
  substitute_user_id: 3,
  substitute_label: "Erika Muster",
  valid_from: "2026-10-20T00:00:00Z",
  valid_until: "2030-11-03T00:00:00Z",
  created_at: "2026-10-12T08:00:00Z",
};
const LIST = "GET /api/v1/admin/approvals/delegations";

describe("ApprovalDelegationsPage", () => {
  it("lists both people of every delegation", async () => {
    const list = vi.fn(() => [D]);
    mockApi({ [LIST]: list });
    renderApproval(<ApprovalDelegationsPage />);
    expect(await screen.findByTestId("admin-delegation-people")).toHaveTextContent("Erika Muster vertritt Alex Turner");
    const q = (list.mock.calls[0] as unknown as [{ query: Record<string, unknown> }])[0].query;
    expect(q).toMatchObject({ limit: 50, offset: 0 });
  });

  it("creates a substitute for another agent", async () => {
    const create = vi.fn(() => D);
    mockApi({ [LIST]: () => [], "POST /api/v1/admin/approvals/delegations": create });
    renderApproval(<ApprovalDelegationsPage />);
    fireEvent.click(await screen.findByTestId("admin-delegation-user"));
    fireEvent.click(await screen.findByTestId("admin-delegation-user-menu-option-2"));
    fireEvent.click(screen.getByTestId("admin-delegation-substitute"));
    fireEvent.click(await screen.findByTestId("admin-delegation-substitute-menu-option-3"));
    fireEvent.click(screen.getByTestId("admin-delegation-create"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const body = (create.mock.calls[0] as unknown as [{ body: Record<string, unknown> }])[0].body;
    expect(body).toEqual({ user_id: 2, substitute_user_id: 3, valid_from: null, valid_until: null });
  });

  it("asks for both people before saving", async () => {
    const create = vi.fn();
    mockApi({ [LIST]: () => [], "POST /api/v1/admin/approvals/delegations": create });
    renderApproval(<ApprovalDelegationsPage />);
    fireEvent.click(await screen.findByTestId("admin-delegation-create"));
    expect(await screen.findByTestId("admin-delegation-missing")).toHaveTextContent("Bitte wählen Sie beide Personen aus.");
    expect(create).not.toHaveBeenCalled();
  });

  it("says an already ended delegation is ended and reloads", async () => {
    const list = vi.fn(() => [D]);
    mockApi({
      [LIST]: list,
      "DELETE /api/v1/admin/approvals/delegations/1": () => {
        throw new ApiError(403, { detail: { code: "delegation_not_active", detail: null } }, "/x");
      },
    });
    renderApproval(<ApprovalDelegationsPage />);
    fireEvent.click(await screen.findByTestId("admin-delegation-revoke"));
    expect(await screen.findByTestId("admin-delegation-ended")).toHaveTextContent("Bereits beendet");
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(screen.queryByTestId("admin-delegation-row-error")).toBeNull();
  });

  it("explains a past end date when the server refuses the period", async () => {
    mockApi({
      [LIST]: () => [],
      "POST /api/v1/admin/approvals/delegations": () => {
        throw new ApiError(422, { detail: { code: "invalid_definition", detail: "valid_until in the past" } }, "/x");
      },
    });
    renderApproval(<ApprovalDelegationsPage />);
    fireEvent.click(await screen.findByTestId("admin-delegation-user"));
    fireEvent.click(await screen.findByTestId("admin-delegation-user-menu-option-2"));
    fireEvent.click(screen.getByTestId("admin-delegation-substitute"));
    fireEvent.click(await screen.findByTestId("admin-delegation-substitute-menu-option-3"));
    fireEvent.change(screen.getByTestId("admin-delegation-until"), { target: { value: "2030-11-03T10:00" } });
    fireEvent.click(screen.getByTestId("admin-delegation-create"));
    expect(await screen.findByTestId("admin-delegation-create-error")).toHaveTextContent("Das Ende muss in der Zukunft liegen.");
  });

  it("pages with limit 50 and offsets", async () => {
    const full = Array.from({ length: 50 }, (_, i) => ({ ...D, id: i + 1 }));
    const list = vi.fn((init: { query?: { offset?: number } } | undefined) => (init?.query?.offset ? [{ ...D, id: 99 }] : full));
    mockApi({ [LIST]: list as never });
    renderApproval(<ApprovalDelegationsPage />);
    await screen.findAllByTestId("admin-delegation-row");
    expect(screen.getAllByTestId("admin-delegation-row")).toHaveLength(50);
    fireEvent.click(screen.getByTestId("admin-delegations-next"));
    await waitFor(() => expect(screen.getAllByTestId("admin-delegation-row")).toHaveLength(1));
    expect((list.mock.calls.at(-1) as unknown as [{ query: { limit: number; offset: number } }])[0].query).toMatchObject({ limit: 50, offset: 50 });
    expect(screen.getByTestId("admin-delegations-next")).toBeDisabled();
  });
});
