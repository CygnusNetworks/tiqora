import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { resolveApprovalsData } from "@/demo/approvalsMock";
import { mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";
import { ApprovalListsPage } from "./ApprovalListsPage";

beforeAll(useGerman);

const LISTS = "GET /api/v1/admin/approvals/lists";
const lists = () => resolveApprovalsData("/api/v1/admin/approvals/lists", "GET");

describe("ApprovalListsPage", () => {
  it("lists groups with their members", async () => {
    mockApi({ [LISTS]: lists });
    renderApproval(<ApprovalListsPage />);
    const row = await screen.findByTestId("list-row");
    expect(row).toHaveTextContent("CAB Netzwerk");
    expect(screen.getByTestId("list-row-members")).toHaveTextContent("Alex Turner, Erika Muster, Max Beispiel");
  });

  it("adds and removes a member and saves the full id list", async () => {
    const put = vi.fn(() => (lists() as unknown[])[0]);
    mockApi({ [LISTS]: lists, "PUT /api/v1/admin/approvals/lists/1": put });
    renderApproval(<ApprovalListsPage />);
    fireEvent.click(await screen.findByTestId("list-edit"));
    const dialog = await screen.findByTestId("list-dialog");
    expect(within(dialog).getAllByTestId("list-member")).toHaveLength(3);
    fireEvent.click(within(dialog).getAllByTestId("list-member-remove")[0]);
    expect(within(dialog).getAllByTestId("list-member")).toHaveLength(2);
    fireEvent.click(screen.getByTestId("list-member-add"));
    fireEvent.click(await screen.findByTestId("list-member-add-menu-option-3"));
    fireEvent.click(screen.getByTestId("list-save"));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    const body = (put.mock.calls[0] as unknown as [{ body: Record<string, unknown> }])[0].body;
    expect(body.member_ids).toEqual([2, 3]);
    expect(body.name).toBe("CAB Netzwerk");
  });

  it("requires a name for a new group", async () => {
    const post = vi.fn();
    mockApi({ [LISTS]: lists, "POST /api/v1/admin/approvals/lists": post });
    renderApproval(<ApprovalListsPage />);
    fireEvent.click(await screen.findByTestId("list-new"));
    fireEvent.click(await screen.findByTestId("list-save"));
    expect(await screen.findByTestId("list-name-error")).toHaveTextContent("Bitte geben Sie einen Namen ein.");
    expect(post).not.toHaveBeenCalled();
  });

  it("explains that a group in use cannot be deleted", async () => {
    mockApi({
      [LISTS]: lists,
      "DELETE /api/v1/admin/approvals/lists/1": () => {
        throw new ApiError(409, { detail: { code: "list_in_use", detail: null } }, "/x");
      },
    });
    renderApproval(<ApprovalListsPage />);
    fireEvent.click(await screen.findByTestId("list-delete"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    const msg = await screen.findByTestId("list-delete-error");
    expect(msg).toHaveTextContent(/verwendet/i);
    expect(screen.getByTestId("list-row")).toBeInTheDocument();
  });

  it("asks before closing an edited group and keeps it open on cancel", async () => {
    mockApi({ [LISTS]: lists });
    renderApproval(<ApprovalListsPage />);
    fireEvent.click(await screen.findByTestId("list-edit"));
    fireEvent.click(await screen.findByTestId("list-cancel"));
    expect(screen.queryByTestId("list-dialog")).toBeNull();
    fireEvent.click(screen.getByTestId("list-edit"));
    fireEvent.change(await screen.findByTestId("list-name"), { target: { value: "CAB Neu" } });
    fireEvent.click(screen.getByTestId("list-cancel"));
    expect(await screen.findByText("Änderungen verwerfen?")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    expect(screen.getByTestId("list-name")).toHaveValue("CAB Neu");
    fireEvent.click(screen.getByTestId("list-cancel"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(screen.queryByTestId("list-dialog")).toBeNull());
  });
});
