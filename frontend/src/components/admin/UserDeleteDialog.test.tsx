import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { UserOut } from "@/lib/api";
import { UserDeleteDialog } from "./UserDeleteDialog";

const { getUserDeletable, deleteUserPermanently } = vi.hoisted(() => ({
  getUserDeletable: vi.fn(),
  deleteUserPermanently: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { getUserDeletable, deleteUserPermanently } };
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

function wrap(onDeleted = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <UserDeleteDialog user={USER} onClose={vi.fn()} onDeleted={onDeleted} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return onDeleted;
}

describe("UserDeleteDialog", () => {
  beforeEach(() => {
    getUserDeletable.mockReset();
    deleteUserPermanently.mockReset().mockResolvedValue(undefined);
  });

  it("offers the delete when nothing references the agent", async () => {
    getUserDeletable.mockResolvedValue({ deletable: true, blocking: [] });
    const onDeleted = wrap();

    await screen.findByTestId("user-delete-deletable");
    const confirm = screen.getByTestId("user-delete-confirm");
    expect(confirm).toBeEnabled();

    fireEvent.click(confirm);
    await waitFor(() => expect(deleteUserPermanently).toHaveBeenCalledWith(7));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });

  it("names the blocking tables and disables the button when still referenced", async () => {
    getUserDeletable.mockResolvedValue({
      deletable: false,
      blocking: [
        { table: "ticket", column: "create_by" },
        { table: "article", column: "change_by" },
      ],
    });
    wrap();

    await screen.findByTestId("user-delete-blocked");
    expect(screen.getByText("ticket.create_by")).toBeInTheDocument();
    expect(screen.getByText("article.change_by")).toBeInTheDocument();
    expect(screen.getByTestId("user-delete-confirm")).toBeDisabled();
    expect(deleteUserPermanently).not.toHaveBeenCalled();
  });
});
