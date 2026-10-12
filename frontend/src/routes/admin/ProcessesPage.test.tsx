import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ProcessesPage } from "./ProcessesPage";

const navigate = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  Link: ({ to, params, children, ...rest }: { to: string; params?: Record<string, string>; children: ReactNode; [k: string]: unknown }) => (
    <a href={Object.entries(params ?? {}).reduce((acc, [k, v]) => acc.replace(`$${k}`, v), to)} {...rest}>
      {children}
    </a>
  ),
}));

const { listProcesses, createProcess, copyProcess, deleteProcess, importFile, status } = vi.hoisted(() => ({
  listProcesses: vi.fn(),
  createProcess: vi.fn(),
  copyProcess: vi.fn(),
  deleteProcess: vi.fn(),
  importFile: vi.fn(),
  status: vi.fn(),
}));
vi.mock("@/lib/processDesignerApi", () => ({
  processDesignerApi: {
    listProcesses,
    createProcess,
    copyProcess,
    deleteProcess,
    importFile,
    exportUrl: (id: string) => `/api/v1/admin/process-designer/processes/${id}/export`,
  },
}));
vi.mock("@/lib/deployStatusApi", () => ({
  deployStatusApi: { status, deploy: vi.fn() },
}));

const row = (entity_id: string, name: string, state_name: string, sync_state = "synced") => ({
  entity_id,
  entity_type: "Process",
  name,
  state_entity_id: "S",
  state_name,
  sync_state,
  used_by: [],
});
const rows = [
  row("Process-a", "Bestellung", "Active"),
  row("Process-b", "Altprozess", "Inactive"),
  row("Process-c", "Auslauf", "FadeAway", "not_sync"),
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ProcessesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ProcessesPage", () => {
  beforeEach(() => {
    navigate.mockReset();
    listProcesses.mockReset().mockResolvedValue(rows);
    createProcess.mockReset();
    copyProcess.mockReset();
    deleteProcess.mockReset().mockResolvedValue(undefined);
    importFile.mockReset();
    status.mockReset().mockResolvedValue({
      process: {
        pending_changes: 3,
        running_live: false,
        revision: { id: 1, source: "initial", entity_count: 3, deployed_at: "2026-10-08T10:00:00Z", deployed_by: null },
        znuny_sync: { state: "not_needed", revision_id: null, message: "", since: null, overdue: false },
        deploy_mode: null,
      },
      acl: {
        pending_changes: 0,
        running_live: false,
        revision: null,
        znuny_sync: { state: "not_needed", revision_id: null, message: "", since: null, overdue: false },
        deploy_mode: null,
      },
      ownership_active: false,
      can_deploy_from_tiqora: false,
    });
  });

  it("lists every process with its state, including inactive and fading ones", async () => {
    renderPage();
    expect(await screen.findByTestId("process-link-Process-a")).toHaveAttribute("href", "/admin/processes/Process-a");
    expect(screen.getByTestId("process-state-Process-a")).toHaveTextContent(i18n.t("admin.processes.states.Active"));
    expect(screen.getByTestId("process-state-Process-b")).toHaveTextContent(i18n.t("admin.processes.states.Inactive"));
    expect(screen.getByTestId("process-state-Process-c")).toHaveTextContent(i18n.t("admin.processes.states.FadeAway"));
  });

  it("marks processes that are not deployed yet with a draft badge", async () => {
    renderPage();
    await screen.findByTestId("process-link-Process-a");
    expect(screen.getByTestId("process-draft-Process-c")).toHaveTextContent(i18n.t("admin.processDesigner.draft"));
    expect(screen.queryByTestId("process-draft-Process-a")).not.toBeInTheDocument();
  });

  it("offers delete only for inactive processes", async () => {
    renderPage();
    await screen.findByTestId("process-link-Process-a");
    expect(screen.queryByTestId("process-delete-Process-a")).not.toBeInTheDocument();
    expect(screen.queryByTestId("process-delete-Process-c")).not.toBeInTheDocument();
    expect(screen.getByTestId("process-delete-Process-b")).toBeInTheDocument();
  });

  it("deletes an inactive process after confirmation", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("process-delete-Process-b"));
    expect(deleteProcess).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(deleteProcess).toHaveBeenCalledWith("Process-b"));
  });

  it("exports through a plain download link", async () => {
    renderPage();
    const link = await screen.findByTestId("process-export-Process-a");
    expect(link).toHaveAttribute("href", "/api/v1/admin/process-designer/processes/Process-a/export");
    expect(link).toHaveAttribute("download");
  });

  it("copies a process and reports the draft", async () => {
    copyProcess.mockResolvedValue({ process: { entity_id: "Process-d", name: "Bestellung (Kopie)" } });
    renderPage();
    fireEvent.click(await screen.findByTestId("process-copy-Process-a"));
    await waitFor(() => expect(copyProcess).toHaveBeenCalledWith("Process-a"));
    expect(await screen.findByTestId("processes-notice")).toHaveTextContent("Bestellung (Kopie)");
  });

  it("creates a new process by name and opens the designer", async () => {
    createProcess.mockResolvedValue({ process: { entity_id: "Process-n", name: "Neu" } });
    renderPage();
    fireEvent.click(await screen.findByTestId("process-new"));
    fireEvent.change(await screen.findByTestId("confirm-dialog-input"), { target: { value: "Neu" } });
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(createProcess).toHaveBeenCalledWith({ name: "Neu", description: "" }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/admin/processes/$processEntityId",
        params: { processEntityId: "Process-n" },
      }),
    );
  });

  it("imports a file and announces the draft", async () => {
    importFile.mockResolvedValue({ name: "Fremd", process_entity_id: "Process-x", created: {}, updated: {} });
    renderPage();
    fireEvent.click(await screen.findByTestId("process-import"));
    const file = new File(["---\nProcess: {}\n"], "p.yml", { type: "text/yaml" });
    fireEvent.change(await screen.findByTestId("import-file"), { target: { files: [file] } });
    fireEvent.click(screen.getByTestId("import-overwrite"));
    fireEvent.click(screen.getByTestId("import-submit"));
    await waitFor(() => expect(importFile).toHaveBeenCalledWith("---\nProcess: {}\n", true));
    expect(await screen.findByTestId("processes-notice")).toHaveTextContent(
      i18n.t("admin.processDesigner.import.success", { name: "Fremd" }),
    );
  });

  it("shows the refusal text when the import is rejected", async () => {
    const { ApiError } = await import("@/lib/api");
    importFile.mockRejectedValue(new ApiError(422, { detail: "Datei zu groß" }, "/x"));
    renderPage();
    fireEvent.click(await screen.findByTestId("process-import"));
    const file = new File(["x"], "p.yaml");
    fireEvent.change(await screen.findByTestId("import-file"), { target: { files: [file] } });
    fireEvent.click(screen.getByTestId("import-submit"));
    expect(await screen.findByTestId("import-error")).toHaveTextContent(
      i18n.t("admin.processDesigner.import.refused", { message: "Datei zu groß" }),
    );
  });

  it("shows the process deploy status card", async () => {
    renderPage();
    await screen.findByTestId("process-link-Process-a");
    expect(await screen.findByTestId("deploy-status-process")).toBeInTheDocument();
    expect(screen.getByTestId("deploy-status-pending")).toHaveTextContent("3");
  });
});
