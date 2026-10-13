import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { Job } from "@/lib/jobsApi";
import { repository } from "@/test/pluginFixtures";
import { RepositoriesTab } from "./RepositoriesTab";

const repositories = vi.fn();
const createRepository = vi.fn();
const updateRepository = vi.fn();
const deleteRepository = vi.fn();
const refreshRepository = vi.fn();
vi.mock("@/lib/pluginsApi", () => ({
  pluginsApi: {
    repositories: (...a: unknown[]) => repositories(...a),
    createRepository: (...a: unknown[]) => createRepository(...a),
    updateRepository: (...a: unknown[]) => updateRepository(...a),
    deleteRepository: (...a: unknown[]) => deleteRepository(...a),
    refreshRepository: (...a: unknown[]) => refreshRepository(...a),
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
    id: 8,
    kind: "core.plugin_repository_refresh",
    progress_done: 0,
    progress_message: null,
    progress_total: null,
    result: null,
    started_at: "2026-10-12T08:00:01Z",
    status: "running",
    title_key: "jobs.kinds.core.plugin_repository_refresh",
    ...overrides,
  };
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RepositoriesTab />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
  for (const fn of [repositories, createRepository, updateRepository, deleteRepository, refreshRepository]) fn.mockReset();
  repositories.mockResolvedValue([repository()]);
  jobState = undefined;
});

describe("RepositoriesTab", () => {
  it("adds a repository with key", async () => {
    createRepository.mockResolvedValue(repository({ id: 2 }));
    renderTab();
    await screen.findByText("Beispiel-Paketquelle");
    fireEvent.click(screen.getByTestId("repository-add"));
    fireEvent.change(await screen.findByTestId("repository-name"), { target: { value: "Intern" } });
    fireEvent.change(screen.getByTestId("repository-url"), { target: { value: "https://intern.example.org/index.json" } });
    fireEvent.change(screen.getByTestId("repository-key"), { target: { value: "ed25519:abc" } });
    fireEvent.click(screen.getByTestId("repository-save"));
    await waitFor(() =>
      expect(createRepository).toHaveBeenCalledWith({
        name: "Intern",
        url: "https://intern.example.org/index.json",
        public_key: "ed25519:abc",
        enabled: true,
      }),
    );
    // The list is reloaded after saving.
    await waitFor(() => expect(repositories.mock.calls.length).toBeGreaterThan(1));
  });

  it("sends an empty key as null and shows the 422 detail", async () => {
    createRepository.mockRejectedValue(new ApiError(422, { detail: "Der Schlüssel ist ungültig." }, "/x"));
    renderTab();
    await screen.findByText("Beispiel-Paketquelle");
    fireEvent.click(screen.getByTestId("repository-add"));
    fireEvent.change(await screen.findByTestId("repository-name"), { target: { value: "Intern" } });
    fireEvent.change(screen.getByTestId("repository-url"), { target: { value: "https://intern.example.org/index.json" } });
    fireEvent.click(screen.getByTestId("repository-save"));
    expect(await screen.findByTestId("repository-error")).toHaveTextContent("Der Schlüssel ist ungültig.");
    expect(createRepository).toHaveBeenCalledWith(expect.objectContaining({ public_key: null }));
  });

  it("shows env repositories read-only", async () => {
    repositories.mockResolvedValue([repository({ id: 7, name: "Betrieb", origin: "env" })]);
    renderTab();
    const row = await screen.findByTestId("repository-row-7");
    expect(within(row).getByText("Aus der Server-Konfiguration")).toBeInTheDocument();
    expect(screen.queryByTestId("repository-edit-7")).toBeNull();
    expect(screen.queryByTestId("repository-delete-7")).toBeNull();
    expect(within(row).getByTestId("repository-check-7")).toBeInTheDocument();
  });

  it("checks a repository now", async () => {
    refreshRepository.mockResolvedValue({ job_id: 8, status: "queued" });
    jobState = job({ status: "running" });
    renderTab();
    fireEvent.click(await screen.findByTestId("repository-check-1"));
    await waitFor(() => expect(refreshRepository).toHaveBeenCalledWith(1));
    expect(await screen.findByTestId("repository-progress-1")).toBeInTheDocument();
  });

  it("reloads the list when the check job has finished", async () => {
    refreshRepository.mockResolvedValue({ job_id: 8, status: "queued" });
    jobState = job({ status: "succeeded" });
    renderTab();
    await screen.findByText("Beispiel-Paketquelle");
    const before = repositories.mock.calls.length;
    fireEvent.click(screen.getByTestId("repository-check-1"));
    await waitFor(() => expect(repositories.mock.calls.length).toBeGreaterThan(before));
  });

  it("enables the check button again once the check job has ended", async () => {
    refreshRepository.mockResolvedValue({ job_id: 8, status: "queued" });
    jobState = job({ status: "failed" });
    renderTab();
    await screen.findByText("Beispiel-Paketquelle");
    fireEvent.click(screen.getByTestId("repository-check-1"));
    await screen.findByTestId("repository-progress-1");
    await waitFor(() => expect(screen.getByTestId("repository-check-1")).not.toBeDisabled());
  });

  it("shows the last error and verification state", async () => {
    repositories.mockResolvedValue([
      repository({ id: 1, verified: true }),
      repository({ id: 2, name: "Kaputt", url: "https://b.example.org/i.json", verified: false, last_ok_at: null, last_error: "Paketquelle nicht erreichbar" }),
    ]);
    renderTab();
    const ok = await screen.findByTestId("repository-row-1");
    expect(within(ok).getByText("Paketliste signiert")).toBeInTheDocument();
    const bad = screen.getByTestId("repository-row-2");
    expect(within(bad).getByText("Paketliste nicht signiert")).toBeInTheDocument();
    expect(within(bad).getByRole("alert")).toHaveTextContent("Paketquelle nicht erreichbar");
  });

  it("deletes an admin repository after confirmation", async () => {
    deleteRepository.mockResolvedValue(undefined);
    renderTab();
    fireEvent.click(await screen.findByTestId("repository-delete-1"));
    expect(deleteRepository).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(deleteRepository).toHaveBeenCalledWith(1));
  });

  it("explains the empty state", async () => {
    repositories.mockResolvedValue([]);
    renderTab();
    expect(await screen.findByText("Es ist noch keine Paketquelle eingerichtet.")).toBeInTheDocument();
  });
});
