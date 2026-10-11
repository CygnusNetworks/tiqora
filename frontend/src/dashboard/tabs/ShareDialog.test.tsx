import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { dashboardsApi } from "@/lib/dashboardsApi";
import { demoDashboard } from "@/demo/dashboardsMock";
import { makeUser } from "@/test/fireUser";
import { ShareDialog } from "./ShareDialog";

const user = makeUser();
const d = demoDashboard({ id: 1, name: "Mein Start" });
const GROUPS = [
  { id: 1, name: "Admin" },
  { id: 2, name: "Support" },
];

const api = {
  share: vi.spyOn(dashboardsApi, "share"),
  shareGroups: vi.spyOn(dashboardsApi, "shareGroups"),
};
const onClose = vi.fn();

function renderDialog(dashboard = d) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ShareDialog dashboard={dashboard} onClose={onClose} />
    </QueryClientProvider>,
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  api.shareGroups.mockResolvedValue(GROUPS);
  api.share.mockResolvedValue(demoDashboard({ ...d, kind: "shared", group_ids: [2] }));
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ShareDialog", () => {
  it("lists the user's share groups, preselects the current ones and saves the selection", async () => {
    renderDialog(demoDashboard({ ...d, kind: "shared", group_ids: [1] }));
    expect(await screen.findByRole("radio", { name: "Mit Gruppen", checked: true })).toBeVisible();
    expect(await screen.findByRole("checkbox", { name: "Admin", checked: true })).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Support", checked: false })).toBeVisible();
    await user.click(screen.getByRole("checkbox", { name: "Admin" }));
    await user.click(screen.getByRole("checkbox", { name: "Support" }));
    await user.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(api.share).toHaveBeenCalledWith(1, [2]));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("'Nur ich' saves an empty list; 'Mit Gruppen' with nothing checked disables Speichern", async () => {
    renderDialog(demoDashboard({ ...d, kind: "shared", group_ids: [2] }));
    await user.click(screen.getByRole("radio", { name: "Nur ich" }));
    await user.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(api.share).toHaveBeenCalledWith(1, []));
    cleanup();
    renderDialog();
    await user.click(screen.getByRole("radio", { name: "Mit Gruppen" }));
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("the search narrows the list of groups", async () => {
    renderDialog();
    await user.click(screen.getByRole("radio", { name: "Mit Gruppen" }));
    await screen.findByRole("checkbox", { name: "Support" });
    await user.type(screen.getByRole("searchbox", { name: "Gruppen suchen" }), "sup");
    expect(screen.queryByRole("checkbox", { name: "Admin" })).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Support" })).toBeVisible();
  });

  it("a 422 from the server shows its message under the list and keeps the dialog open", async () => {
    api.share.mockRejectedValue(
      new ApiError(
        422,
        { detail: { message: "Mit der Gruppe Support kann nicht geteilt werden.", path: "group_ids" } },
        "/api/v1/dashboards/1/share",
      ),
    );
    renderDialog();
    await user.click(screen.getByRole("radio", { name: "Mit Gruppen" }));
    await user.click(await screen.findByRole("checkbox", { name: "Support" }));
    await user.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Mit der Gruppe Support kann nicht geteilt werden.")).toBeVisible();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("the viewer-scope note is shown", async () => {
    renderDialog(demoDashboard({ ...d, kind: "shared", group_ids: [2] }));
    expect(await screen.findByText(/nur Tickets aus Queues, die sie selbst lesen dürfen/)).toBeVisible();
  });
});
