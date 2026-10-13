import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { catalogEntry, repository } from "@/test/pluginFixtures";
import { CatalogTab } from "./CatalogTab";

const catalog = vi.fn();
const repositories = vi.fn();
vi.mock("@/lib/pluginsApi", () => ({
  pluginsApi: {
    catalog: (...a: unknown[]) => catalog(...a),
    repositories: (...a: unknown[]) => repositories(...a),
  },
}));

const onDownload = vi.fn();

function renderTab(runtimeInstall = true, downloading = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CatalogTab runtimeInstall={runtimeInstall} downloading={downloading} onDownload={onDownload} />
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
  for (const fn of [catalog, repositories, onDownload]) fn.mockReset();
  catalog.mockResolvedValue(ENTRIES);
  repositories.mockResolvedValue([repository()]);
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

  it("hands the newest compatible release to the page's download job", async () => {
    renderTab();
    fireEvent.click(await screen.findByTestId("catalog-action-org.example.old"));
    expect(onDownload).toHaveBeenCalledWith(1, "org.example.old", "1.1.0");
  });

  it("disables the actions while a download runs", async () => {
    renderTab(true, true);
    await screen.findByText("Neu-Plugin");
    expect(screen.getByTestId("catalog-action-org.example.new")).toBeDisabled();
    expect(screen.getByTestId("catalog-action-org.example.old")).toBeDisabled();
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
