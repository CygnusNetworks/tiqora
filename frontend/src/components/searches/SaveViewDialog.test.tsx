import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { savedSearchesApi } from "@/lib/savedSearchesApi";
import { SaveViewDialog, type SaveViewParams } from "./SaveViewDialog";
import { savedSearch as s, user } from "./searchTestUtils";
import { toTableSortKey, toTicketSortKey } from "./searchSort";

function renderDialog(listParams: SaveViewParams, extra: { onClose?: () => void; onSaved?: (x: unknown) => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SaveViewDialog open onClose={extra.onClose ?? vi.fn()} onSaved={extra.onSaved} listParams={listParams} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => vi.restoreAllMocks());

describe("SaveViewDialog", () => {
  it("posts the filters and the sort, but not the page", async () => {
    const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s({ id: 3, name: "Wartend" }));
    const onSaved = vi.fn();
    const onClose = vi.fn();
    renderDialog(
      { queue_id: 4, state_type: "pending", unassigned: true, offset: 100, limit: 50, sort: "changed", order: "asc" },
      { onSaved, onClose },
    );
    await user.type(screen.getByTestId("save-view-name"), "Wartend");
    await user.click(screen.getByTestId("save-view-confirm"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toEqual({
      name: "Wartend",
      list_params: { queue_id: 4, state_type: "pending", unassigned: true, include_archived: false },
      sort_by: "changed",
      sort_order: "asc",
      include_archived: false,
      show_in_sidebar: true,
      show_count: false,
    });
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: 3 })));
    expect(onClose).toHaveBeenCalled();
  });

  it("maps the list's owner sort to a sort the backend knows", async () => {
    const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s({ id: 3 }));
    renderDialog({ queue_id: 1, sort: "owner", order: "asc" });
    await user.type(screen.getByTestId("save-view-name"), "Nach Besitzer");
    await user.click(screen.getByTestId("save-view-confirm"));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0]).toMatchObject({ sort_by: "activity", sort_order: "asc" });
  });

  it("keeps archived tickets and the count switch with the sidebar choice", async () => {
    const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s({ id: 3 }));
    renderDialog({ include_archived: true });
    await user.type(screen.getByTestId("save-view-name"), "Mit Archiv");
    await user.click(screen.getByTestId("save-view-count"));
    await user.click(screen.getByTestId("save-view-confirm"));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0]).toMatchObject({
      include_archived: true,
      list_params: { include_archived: true },
      show_in_sidebar: true,
      show_count: true,
    });
  });

  it("does not save without a name", async () => {
    const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s());
    renderDialog({});
    await user.click(screen.getByTestId("save-view-confirm"));
    expect(await screen.findByTestId("save-view-name-error")).toHaveTextContent("Namen");
    expect(create).not.toHaveBeenCalled();
  });

  it("shows the server's message when saving fails", async () => {
    vi.spyOn(savedSearchesApi, "create").mockRejectedValue(
      new ApiError(422, { detail: { message: "Zu viele gespeicherte Suchen." } }, "/api/v1/saved-searches"),
    );
    renderDialog({});
    await user.type(screen.getByTestId("save-view-name"), "X");
    await user.click(screen.getByTestId("save-view-confirm"));
    expect(await screen.findByTestId("save-view-error")).toHaveTextContent("Zu viele gespeicherte Suchen.");
  });
});

describe("sort key mapping", () => {
  it("maps the list's own sort keys to backend keys", () => {
    expect(toTicketSortKey("state")).toBe("activity");
    expect(toTicketSortKey("customer")).toBe("activity");
    expect(toTicketSortKey("owner")).toBe("activity");
    expect(toTicketSortKey("deadline")).toBe("deadline");
    expect(toTicketSortKey(undefined)).toBe("activity");
    expect(toTableSortKey("created")).toBe("age");
    expect(toTableSortKey("tn")).toBe("tn");
  });
});
