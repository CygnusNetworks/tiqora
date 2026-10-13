import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { Job } from "@/lib/jobsApi";
import { packageReport, restartStatus } from "@/test/pluginFixtures";
import { InstallDialog } from "./InstallDialog";

const installPackage = vi.fn();
const restartStatusFn = vi.fn();
vi.mock("@/lib/pluginsApi", () => ({
  pluginsApi: {
    installPackage: (...a: unknown[]) => installPackage(...a),
    restartStatus: (...a: unknown[]) => restartStatusFn(...a),
  },
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
    id: 7,
    kind: "core.plugin_install",
    progress_done: 0,
    progress_message: "Datenbank wird angepasst",
    progress_total: null,
    result: null,
    started_at: "2026-10-12T08:00:01Z",
    status: "running",
    title_key: "jobs.kinds.core.plugin_install",
    ...overrides,
  };
}

function renderDialog(pkg = packageReport(), coordinatedRestart = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <InstallDialog pkg={pkg} open onClose={vi.fn()} coordinatedRestart={coordinatedRestart} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
  installPackage.mockReset();
  restartStatusFn.mockReset();
  restartStatusFn.mockResolvedValue(restartStatus({ generation: 4 }));
  jobState = undefined;
});

describe("InstallDialog", () => {
  it("shows capabilities, tables and the trust note", async () => {
    renderDialog(packageReport({ capabilities: [{ kind: "table", value: "tqp_tables_note" }] }));
    expect(await screen.findByText("Legt die Tabelle tqp_tables_note an")).toBeInTheDocument();
    expect(screen.getByText(/keine Absicherung/)).toBeInTheDocument();
    expect(screen.getByText("Verifizierter Herausgeber")).toBeInTheDocument();
    expect(screen.getByText("Signiert von Example Org")).toBeInTheDocument();
  });

  it("summarises more than three tables and names them in the details", () => {
    const capabilities = ["a", "b", "c", "d"].map((n) => ({ kind: "table" as const, value: `tqp_tables_${n}` }));
    renderDialog(packageReport({ capabilities }));
    expect(screen.getByText("Legt 4 Tabellen an")).toBeInTheDocument();
    expect(screen.getByText("tqp_tables_d")).toBeInTheDocument();
    expect(screen.queryByText("Legt die Tabelle tqp_tables_a an")).toBeNull();
  });

  it("blocks install when there are problems", () => {
    renderDialog(packageReport({ installable: false, problems: ["Kurzname „tables“ wird schon von org.other.tables verwendet"] }));
    expect(screen.getByRole("button", { name: "Installieren" })).toBeDisabled();
    expect(screen.getByTestId("plugin-install-problems")).toHaveTextContent("wird schon von org.other.tables verwendet");
  });

  it("requires the trust checkbox for unsigned packages", () => {
    renderDialog(packageReport({ trust: "unsigned", signer: null, warnings: ["Das Paket ist nicht signiert."] }));
    const submit = screen.getByRole("button", { name: "Installieren" });
    expect(submit).toBeDisabled();
    expect(screen.getByText("Das Paket ist nicht signiert.")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Ich vertraue diesem Paket"));
    expect(submit).toBeEnabled();
  });

  it("hides the enable checkbox for updates and names both versions", () => {
    renderDialog(packageReport({ action: "update", installed_version: "1.0.0", version: "1.1.0" }));
    expect(screen.getByText("Aktualisieren von 1.0.0 auf 1.1.0")).toBeInTheDocument();
    expect(screen.queryByLabelText("Nach der Installation einschalten")).toBeNull();
    expect(screen.getByLabelText("Tiqora danach automatisch neu starten")).toBeChecked();
  });

  it("starts the job with the chosen options and shows progress, then the restart panel", async () => {
    installPackage.mockResolvedValue({ job_id: 7, status: "queued" });
    jobState = job();
    renderDialog();
    fireEvent.click(screen.getByLabelText("Nach der Installation einschalten"));
    fireEvent.click(screen.getByRole("button", { name: "Installieren" }));
    await waitFor(() => expect(installPackage).toHaveBeenCalledWith("a".repeat(64), { enable: false, restart: true }));
    expect(await screen.findByTestId("plugin-install-progress")).toHaveTextContent("Datenbank wird angepasst");
    expect(screen.queryByTestId("plugin-restart-running")).toBeNull();
  });

  it("shows the restart panel once the job succeeded", async () => {
    installPackage.mockResolvedValue({ job_id: 7, status: "queued" });
    // The job recorded restart generation 5; generation 4 is the old, finished one.
    jobState = job({ status: "succeeded", finished_at: "2026-10-12T08:00:20Z", result: { restart_generation: 5 } });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Installieren" }));
    expect(await screen.findByText("Installiert.")).toBeInTheDocument();
    expect(await screen.findByTestId("plugin-restart-running")).toHaveTextContent("Tiqora wird neu gestartet");
    expect(installPackage).toHaveBeenCalledWith("a".repeat(64), { enable: true, restart: true });
  });

  it("finishes at once when the recorded restart is already over (stateless demo)", async () => {
    installPackage.mockResolvedValue({ job_id: 7, status: "queued" });
    jobState = job({ status: "succeeded", result: { restart_generation: 4 } });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Installieren" }));
    expect(await screen.findByTestId("plugin-restart-done")).toHaveTextContent("Tiqora wurde neu gestartet.");
  });

  it("asks for a manual restart when the job could not request one", async () => {
    installPackage.mockResolvedValue({ job_id: 7, status: "queued" });
    jobState = job({ status: "succeeded", result: { restart_generation: null } });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Installieren" }));
    expect(await screen.findByTestId("plugin-restart-manual")).toBeInTheDocument();
    expect(screen.queryByTestId("plugin-restart-running")).toBeNull();
    expect(restartStatusFn).not.toHaveBeenCalled();
  });

  it("says the install failed instead of 'wird installiert' after a failed job", async () => {
    installPackage.mockResolvedValue({ job_id: 7, status: "queued" });
    jobState = job({ status: "failed", error: "Prüfung fehlgeschlagen: kaputt" });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Installieren" }));
    expect(await screen.findByTestId("plugin-install-headline")).toHaveTextContent(
      "Die Installation wurde nicht abgeschlossen.",
    );
  });

  it("refreshes the list when closed while the job still runs", async () => {
    installPackage.mockResolvedValue({ job_id: 7, status: "queued" });
    jobState = job();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    const onClose = vi.fn();
    render(
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <InstallDialog pkg={packageReport()} open onClose={onClose} coordinatedRestart />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Installieren" }));
    fireEvent.click(await screen.findByTestId("plugin-install-close"));
    expect(onClose).toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["admin", "plugins"] });
  });

  it("hides the restart checkbox when coordinated restart is off", async () => {
    installPackage.mockResolvedValue({ job_id: 7, status: "queued" });
    renderDialog(packageReport(), false);
    expect(screen.queryByLabelText("Tiqora danach automatisch neu starten")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Installieren" }));
    await waitFor(() => expect(installPackage).toHaveBeenCalledWith("a".repeat(64), { enable: true, restart: false }));
    expect(restartStatusFn).not.toHaveBeenCalled();
  });

  it("shows the server's message when starting the install fails", async () => {
    installPackage.mockRejectedValue(new Error("x"));
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Installieren" }));
    expect(await screen.findByTestId("plugin-install-error")).toHaveTextContent(
      "Die Änderung konnte nicht gespeichert werden.",
    );
  });
});
