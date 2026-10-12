import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { RevisionsPanel } from "./RevisionsPanel";

const { revisions, revisionDiff, restoreRevision } = vi.hoisted(() => ({
  revisions: vi.fn(),
  revisionDiff: vi.fn(),
  restoreRevision: vi.fn(),
}));
vi.mock("@/lib/processDesignerApi", () => ({ processDesignerApi: { revisions, revisionDiff, restoreRevision } }));

const rev = (id: number, source: string, by: number | null = null) => ({
  id,
  source,
  entity_count: 3,
  deployed_at: `2026-10-0${id}T10:00:00Z`,
  deployed_by: by,
});

function renderPanel(onRestored = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RevisionsPanel processEntityId="Process-1" onRestored={onRestored} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return onRestored;
}

describe("RevisionsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    revisions.mockResolvedValue([rev(1, "initial"), rev(3, "tiqora", 7), rev(2, "znuny")]);
    revisionDiff.mockResolvedValue({
      from_revision: 2,
      to_revision: 3,
      process_entity_id: "Process-1",
      changes: [{ change: "added", entity_type: "Activity", entity_id: "Activity-n", name: "Neu", changed_keys: [] }],
      yaml_before: "a: 1\n",
      yaml_after: "a: 1\nb: 2\n",
    });
  });

  it("lists the versions newest first with their origin and author", async () => {
    renderPanel();
    const list = await screen.findByTestId("revisions-list");
    const rows = Array.from(list.querySelectorAll("li")).map((li) => li.dataset.testid);
    expect(rows).toEqual(["revision-3", "revision-2", "revision-1"]);
    expect(screen.getByTestId("revision-3")).toHaveTextContent(i18n.t("admin.deploy.source.tiqora"));
    expect(screen.getByTestId("revision-3")).toHaveTextContent(i18n.t("admin.processDesigner.revisions.by", { id: 7 }));
    expect(screen.getByTestId("revision-2")).toHaveTextContent(i18n.t("admin.deploy.source.znuny"));
    expect(screen.getByTestId("revision-1")).toHaveTextContent(i18n.t("admin.deploy.source.initial"));
  });

  it("compares with the previous version by default and renders the diff", async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId("revision-compare-3"));
    expect(await screen.findByTestId("revision-diff-change-added")).toHaveTextContent("Neu");
    expect(revisionDiff).toHaveBeenCalledWith(3, { against: undefined, process: "Process-1" }, expect.anything());
  });

  it("compares with a picked version", async () => {
    renderPanel();
    await screen.findByTestId("revisions-list");
    fireEvent.change(screen.getByTestId("revisions-base"), { target: { value: "1" } });
    fireEvent.click(screen.getByTestId("revision-compare-3"));
    await screen.findByTestId("revision-diff");
    expect(revisionDiff).toHaveBeenCalledWith(3, { against: 1, process: "Process-1" }, expect.anything());
  });

  it("restores only after confirmation and hands the draft to the page", async () => {
    const draft = { process: { name: "restored" } };
    restoreRevision.mockResolvedValue(draft);
    const onRestored = renderPanel();
    fireEvent.click(await screen.findByTestId("revision-restore-2"));
    // nothing happens before the dialog is confirmed
    expect(restoreRevision).not.toHaveBeenCalled();
    const dialog = await screen.findByTestId("confirm-dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: i18n.t("admin.processDesigner.revisions.restore") }));
    await waitFor(() => expect(restoreRevision).toHaveBeenCalledWith(2, "Process-1"));
    await waitFor(() => expect(onRestored).toHaveBeenCalledWith(draft));
  });

  it("does not restore when the confirmation is cancelled", async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId("revision-restore-2"));
    const dialog = await screen.findByTestId("confirm-dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: i18n.t("common.cancel") }));
    await new Promise((r) => setTimeout(r, 30));
    expect(restoreRevision).not.toHaveBeenCalled();
  });

  it("shows the empty state", async () => {
    revisions.mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByTestId("revisions-empty")).toBeInTheDocument();
  });
});
