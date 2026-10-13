import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { Job } from "@/lib/jobsApi";
import { catalogEntry, packageReport, repository } from "@/test/pluginFixtures";
import { CatalogTab } from "./CatalogTab";

const catalog = vi.fn();
const repositories = vi.fn();
const download = vi.fn();
const getPackage = vi.fn();
vi.mock("@/lib/pluginsApi", () => ({
  pluginsApi: {
    catalog: (...a: unknown[]) => catalog(...a),
    repositories: (...a: unknown[]) => repositories(...a),
    download: (...a: unknown[]) => download(...a),
    getPackage: (...a: unknown[]) => getPackage(...a),
  },
}));

vi.mock("./InstallDialog", () => ({
  InstallDialog: ({ pkg }: { pkg: { plugin_id: string; version: string } }) => (
    <div data-testid="install-dialog">{`${pkg.plugin_id}@${pkg.version}`}</div>
  ),
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
    id: 5,
    kind: "core.plugin_fetch",
    progress_done: 0,
    progress_message: null,
    progress_total: null,
    result: null,
    started_at: "2026-10-12T08:00:01Z",
    status: "running",
    title_key: "jobs.kinds.core.plugin_fetch",
    ...overrides,
  };
}

function renderTab(runtimeInstall = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CatalogTab runtimeInstall={runtimeInstall} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const ENTRIES = {
  categories: ["tickets", "telefonie"],
  entries: [
    catalogEntry({ plugin_id: "org.example.new", name: "Neu-Plugin" }),
    catalogEntry({
      plugin_id: "org.example.same",
      name: "Gleich-Plugin",
      installed_version: "1.0.0",
      latest_version: "1.0.0",
      latest_compatible: "1.0.0",
    }),
    catalogEntry({
      plugin_id: "org.example.old",
      name: "Alt-Plugin",
      installed_version: "1.0.0",
      latest_version: "1.1.0",
      latest_compatible: "1.1.0",
      update_available: true,
    }),
    catalogEntry({ plugin_id: "org.example.image", name: "Image-Plugin", image_plugin: true, installed_version: "1.0.0" }),
    catalogEntry({ plugin_id: "org.example.future", name: "Zukunfts-Plugin", latest_version: "9.0.0", latest_compatible: null }),
  ],
};

beforeEach(async () => {
  await i18n.changeLanguage("de");
  for (const fn of [catalog, repositories, download, getPackage]) fn.mockReset();
  catalog.mockResolvedValue(ENTRIES);
  repositories.mockResolvedValue([repository()]);
  jobState = undefined;
});

describe("CatalogTab", () => {
  it("searches and filters the catalogue", async () => {
    renderTab();
    await screen.findByText("Neu-Plugin");
    expect(catalog).toHaveBeenLastCalledWith({}, expect.anything());
    fireEvent.change(screen.getByTestId("catalog-search"), { target: { value: "hallo" } });
    // Debounced: the filter is not sent on the keystroke itself.
    expect(catalog).not.toHaveBeenCalledWith({ q: "hallo" }, expect.anything());
    await waitFor(() => expect(catalog).toHaveBeenLastCalledWith({ q: "hallo" }, expect.anything()));
    fireEvent.change(screen.getByTestId("catalog-search"), { target: { value: "" } });
    await waitFor(() => expect(catalog).toHaveBeenLastCalledWith({}, expect.anything()));
    fireEvent.click(screen.getByTestId("catalog-category"));
    fireEvent.click(await screen.findByTestId("catalog-category-menu-option-tickets"));
    await waitFor(() => expect(catalog).toHaveBeenLastCalledWith({ category: "tickets" }, expect.anything()));
  });

  it("shows the right action per entry", async () => {
    renderTab();
    await screen.findByText("Neu-Plugin");
    expect(screen.getByTestId("catalog-action-org.example.new")).toHaveTextContent("Installieren");
    expect(screen.getByTestId("catalog-action-org.example.new")).toBeEnabled();
    const same = screen.getByTestId("catalog-action-org.example.same");
    expect(same).toHaveTextContent("Installiert");
    expect(same).toBeDisabled();
    expect(screen.getByTestId("catalog-action-org.example.old")).toHaveTextContent("Aktualisieren");
    expect(screen.getByTestId("catalog-action-org.example.old")).toBeEnabled();
    const image = screen.getByTestId("catalog-action-org.example.image");
    expect(image).toHaveTextContent("Im Image eingebaut");
    expect(image).toBeDisabled();
    const future = screen.getByTestId("catalog-action-org.example.future");
    expect(future).toHaveTextContent("Passt nicht zu dieser Tiqora-Version");
    expect(future).toBeDisabled();
  });

  it("disables installing when runtime install is off", async () => {
    renderTab(false);
    await screen.findByText("Neu-Plugin");
    expect(screen.getByTestId("catalog-action-org.example.new")).toBeDisabled();
  });

  it("installs from a repository via download job and install dialog", async () => {
    download.mockResolvedValue({ job_id: 5, status: "queued" });
    getPackage.mockResolvedValue(packageReport({ plugin_id: "org.example.old", version: "1.1.0" }));
    const view = renderTab();
    fireEvent.click(await screen.findByTestId("catalog-action-org.example.old"));
    await waitFor(() =>
      expect(download).toHaveBeenCalledWith(1, { plugin_id: "org.example.old", version: "1.1.0" }),
    );
    jobState = job({ status: "succeeded", result: { package_sha256: "b".repeat(64) } });
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}>
          <CatalogTab runtimeInstall />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("install-dialog")).toHaveTextContent("org.example.old@1.1.0");
    expect(getPackage).toHaveBeenCalledWith("b".repeat(64));
  });

  it("shows the failed download job's reason", async () => {
    download.mockResolvedValue({ job_id: 5, status: "queued" });
    jobState = job({ status: "failed", error: "Prüfsumme stimmt nicht mit der Paketquelle überein" });
    renderTab();
    fireEvent.click(await screen.findByTestId("catalog-action-org.example.new"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Prüfsumme stimmt nicht mit der Paketquelle überein");
    expect(screen.queryByTestId("install-dialog")).toBeNull();
  });

  it("explains when no repository is configured", async () => {
    repositories.mockResolvedValue([]);
    catalog.mockResolvedValue({ categories: [], entries: [] });
    renderTab();
    expect(await screen.findByText("Es ist noch keine Paketquelle eingerichtet.")).toBeInTheDocument();
    expect(screen.queryByText("Keine Erweiterungen gefunden.")).toBeNull();
  });

  it("says so when the filter finds nothing", async () => {
    catalog.mockResolvedValue({ categories: [], entries: [] });
    renderTab();
    expect(await screen.findByText("Keine Erweiterungen gefunden.")).toBeInTheDocument();
  });
});
