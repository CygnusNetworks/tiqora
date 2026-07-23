import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AiSettingsPage } from "./AiSettingsPage";

const getSettings = vi.fn();
const putSettings = vi.fn();
const listProviders = vi.fn();
const listMcpClients = vi.fn();
const listQueuePolicies = vi.fn();
const listAcl = vi.fn();
const createAcl = vi.fn();
const updateAcl = vi.fn();
const deleteAcl = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    path: string;
    constructor(status: number, detail: unknown, path: string) {
      super(typeof detail === "string" ? detail : `HTTP ${status}`);
      this.name = "ApiError";
      this.status = status;
      this.path = path;
    }
  },
  api: {
    adminGroups: {
      list: () =>
        Promise.resolve({
          items: [
            { id: 3, name: "users", comments: null, valid_id: 1 },
            { id: 7, name: "support", comments: null, valid_id: 1 },
          ],
          total: 2,
          page: 1,
          page_size: 500,
        }),
    },
    adminRoles: {
      list: () => Promise.resolve({ items: [], total: 0, page: 1, page_size: 500 }),
    },
    listReferenceAgents: () => Promise.resolve([]),
  },
}));

vi.mock("@/lib/aiApi", () => ({
  aiApi: {
    getSettings: (...args: unknown[]) => getSettings(...args),
    putSettings: (...args: unknown[]) => putSettings(...args),
    listProviders: (...args: unknown[]) => listProviders(...args),
    listMcpClients: (...args: unknown[]) => listMcpClients(...args),
    listQueuePolicies: (...args: unknown[]) => listQueuePolicies(...args),
    listAcl: (...args: unknown[]) => listAcl(...args),
    createAcl: (...args: unknown[]) => createAcl(...args),
    updateAcl: (...args: unknown[]) => updateAcl(...args),
    deleteAcl: (...args: unknown[]) => deleteAcl(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AiSettingsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiSettingsPage", () => {
  beforeEach(() => {
    getSettings.mockReset();
    putSettings.mockReset();
    listProviders.mockReset();
    listMcpClients.mockReset();
    listQueuePolicies.mockReset();
    listAcl.mockReset();
    createAcl.mockReset();
    updateAcl.mockReset();
    deleteAcl.mockReset();

    getSettings.mockResolvedValue({
      operation_mode: "tiqora_primary",
      disclosure_default_text: "Diese Antwort wurde von einer KI erstellt.",
      global_max_replies_per_hour: null,
    });
    listProviders.mockResolvedValue({ items: [{ id: 1 }], total: 1, page: 1, page_size: 1 });
    listMcpClients.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 0 });
    listQueuePolicies.mockResolvedValue({
      items: [
        {
          id: 1,
          queue_id: 1,
          enabled_auto_reply: false,
          enabled_summary: true,
          enabled_manual_assist: false,
        },
      ],
      total: 1,
      page: 1,
      page_size: 1,
    });
    listAcl.mockResolvedValue([]);
  });

  it("renders and shows no parallel banner when tiqora_primary", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-settings-page")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("admin-ai-parallel-banner")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-stat-providers").textContent).toBe("1");
    });
    expect(screen.getByTestId("admin-ai-stat-policies").textContent).toBe("1");
  });

  it("shows the parallel banner when operation_mode is parallel", async () => {
    getSettings.mockResolvedValue({
      operation_mode: "parallel",
      disclosure_default_text: "",
      global_max_replies_per_hour: null,
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-parallel-banner")).toBeInTheDocument();
    });
  });

  it("asks for confirmation before switching operation mode, then PUTs it", async () => {
    putSettings.mockResolvedValue({
      operation_mode: "parallel",
      disclosure_default_text: "",
      global_max_replies_per_hour: null,
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-mode-parallel")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-ai-mode-parallel"));
    expect(screen.getByTestId("admin-ai-mode-confirm")).toBeInTheDocument();
    expect(putSettings).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("admin-ai-mode-confirm-apply"));
    await waitFor(() => {
      expect(putSettings).toHaveBeenCalledWith({ operation_mode: "parallel" });
    });
  });

  it("saves disclosure text and global cap", async () => {
    putSettings.mockResolvedValue({
      operation_mode: "tiqora_primary",
      disclosure_default_text: "Neuer Text",
      global_max_replies_per_hour: 20,
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-disclosure-text")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("admin-ai-disclosure-text"), {
      target: { value: "Neuer Text" },
    });
    fireEvent.change(screen.getByTestId("admin-ai-global-cap"), { target: { value: "20" } });
    fireEvent.click(screen.getByTestId("admin-ai-settings-save"));

    await waitFor(() => {
      expect(putSettings).toHaveBeenCalledWith({
        disclosure_default_text: "Neuer Text",
        global_max_replies_per_hour: 20,
      });
    });
  });

});
