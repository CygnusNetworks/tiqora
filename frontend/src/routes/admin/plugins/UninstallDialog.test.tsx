import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { Job } from "@/lib/jobsApi";
import { adminPluginRow, restartStatus } from "@/test/pluginFixtures";
import { UninstallDialog } from "./UninstallDialog";

const uninstall = vi.fn();
const restartStatusFn = vi.fn();
vi.mock("@/lib/pluginsApi", () => ({
  pluginsApi: {
    uninstall: (...a: unknown[]) => uninstall(...a),
    restartStatus: (...a: unknown[]) => restartStatusFn(...a),
  },
}));

const getSection = vi.fn();
vi.mock("@/lib/settingsSectionsApi", () => ({
  settingsSectionsApi: { get: (...a: unknown[]) => getSection(...a) },
}));

let jobState: Job | undefined;
vi.mock("@/components/jobs/useJob", () => ({
  useJob: (id: number | null) => ({
    job: id == null ? undefined : jobState,
    isTerminal: jobState ? ["succeeded", "failed", "cancelled"].includes(jobState.status) : false,
    cancel: vi.fn(),
    isCancelling: false,
  }),
}));

function job(overrides: Partial<Job> = {}): Job {
  return {
    cancel_requested: false,
    created_at: "2026-10-12T08:00:00Z",
    created_by: 1,
    error: null,
    files: [],
    finished_at: null,
    id: 990001,
    kind: "core.plugin_uninstall",
    progress_done: 0,
    progress_message: null,
    progress_total: null,
    result: null,
    started_at: "2026-10-12T08:00:01Z",
    status: "running",
    title_key: "jobs.kinds.core.plugin_uninstall",
    ...overrides,
  };
}

const PLUGIN = adminPluginRow({ id: "org.example.tables", name: "Tabellen-Beispiel", source: "upload", can_uninstall: true });

function renderDialog(coordinatedRestart = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <UninstallDialog plugin={PLUGIN} open onClose={vi.fn()} coordinatedRestart={coordinatedRestart} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
  uninstall.mockReset();
  restartStatusFn.mockReset();
  restartStatusFn.mockResolvedValue(restartStatus({ generation: 4 }));
  getSection.mockReset();
  getSection.mockResolvedValue({ values: { max_concurrency: 2, retention_days: 21 } });
  jobState = undefined;
});

describe("UninstallDialog", () => {
  it("uninstalls keeping data by default", async () => {
    uninstall.mockResolvedValue({ job_id: 990001 });
    renderDialog();
    expect(screen.getByLabelText(/Daten behalten/)).toBeChecked();
    fireEvent.click(screen.getByTestId("plugin-uninstall-submit"));
    await waitFor(() =>
      expect(uninstall).toHaveBeenCalledWith("org.example.tables", { delete_data: false, restart: true }),
    );
    expect(await screen.findByTestId("plugin-uninstall-job")).toHaveTextContent("Erweiterung wird deinstalliert");
  });

  it("does not ask for a restart when restarts are not coordinated", async () => {
    uninstall.mockResolvedValue({ job_id: 990001 });
    renderDialog(false);
    expect(screen.queryByTestId("plugin-uninstall-restart")).toBeNull();
    fireEvent.click(screen.getByTestId("plugin-uninstall-submit"));
    await waitFor(() =>
      expect(uninstall).toHaveBeenCalledWith("org.example.tables", { delete_data: false, restart: false }),
    );
  });

  it("deletes data, names the retention time and offers the backup download", async () => {
    uninstall.mockResolvedValue({ job_id: 990001 });
    renderDialog();
    fireEvent.click(screen.getByLabelText(/Daten löschen/));
    await waitFor(() => expect(screen.getByTestId("plugin-uninstall-delete-hint")).toHaveTextContent("21 Tage"));
    fireEvent.click(screen.getByTestId("plugin-uninstall-restart"));
    jobState = job({
      status: "succeeded",
      files: [{ id: 7, name: "tables-backup.sql.gz", content_type: "application/gzip", size_bytes: 1234 }],
    });
    fireEvent.click(screen.getByTestId("plugin-uninstall-submit"));
    await waitFor(() =>
      expect(uninstall).toHaveBeenCalledWith("org.example.tables", { delete_data: true, restart: false }),
    );
    const link = await screen.findByTestId("plugin-uninstall-backup-7");
    expect(link).toHaveAttribute("href", "/api/v1/jobs/990001/files/7");
    expect(screen.getByTestId("plugin-uninstall-backup")).toHaveTextContent("21 Tage aufbewahrt");
  });

  it("follows the restart the job recorded and says when it failed", async () => {
    uninstall.mockResolvedValue({ job_id: 990001 });
    jobState = job({ status: "succeeded", result: { restart_generation: 5 } });
    renderDialog();
    fireEvent.click(screen.getByTestId("plugin-uninstall-submit"));
    expect(await screen.findByTestId("plugin-restart-running")).toHaveTextContent("Tiqora wird neu gestartet");
    expect(screen.getByTestId("plugin-uninstall-headline")).toHaveTextContent("Deinstalliert.");
  });

  it("says the uninstall failed after a failed job", async () => {
    uninstall.mockResolvedValue({ job_id: 990001 });
    jobState = job({ status: "failed", error: "kaputt" });
    renderDialog();
    fireEvent.click(screen.getByTestId("plugin-uninstall-submit"));
    expect(await screen.findByTestId("plugin-uninstall-headline")).toHaveTextContent(
      "Die Erweiterung wurde nicht deinstalliert.",
    );
    expect(screen.queryByTestId("plugin-restart-running")).toBeNull();
  });

  it("names a retention of one day in the singular", async () => {
    getSection.mockResolvedValue({ values: { retention_days: 1 } });
    renderDialog();
    fireEvent.click(screen.getByLabelText(/Daten löschen/));
    await waitFor(() => expect(screen.getByTestId("plugin-uninstall-delete-hint")).toHaveTextContent("einen Tag"));
    expect(screen.getByTestId("plugin-uninstall-delete-hint")).not.toHaveTextContent("1 Tage");
  });

  it("falls back to 14 days when the retention setting cannot be read", async () => {
    getSection.mockRejectedValue(new Error("x"));
    renderDialog();
    fireEvent.click(screen.getByLabelText(/Daten löschen/));
    expect(screen.getByTestId("plugin-uninstall-delete-hint")).toHaveTextContent("14 Tage");
  });

  it("shows the error when the request is refused", async () => {
    uninstall.mockRejectedValue(new Error("x"));
    renderDialog();
    fireEvent.click(screen.getByTestId("plugin-uninstall-submit"));
    expect(await screen.findByTestId("plugin-uninstall-error")).toHaveTextContent(
      "Die Änderung konnte nicht gespeichert werden.",
    );
  });
});
