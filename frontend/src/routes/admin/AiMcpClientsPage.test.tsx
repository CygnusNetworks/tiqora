import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AiMcpClientsPage } from "./AiMcpClientsPage";

const listMcpClients = vi.fn();
const createMcpClient = vi.fn();
const updateMcpClient = vi.fn();
const deleteMcpClient = vi.fn();
const discoverMcpTools = vi.fn();
const listMcpToolPolicies = vi.fn();
const updateMcpToolPolicy = vi.fn();

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
}));

vi.mock("@/lib/aiApi", () => ({
  aiApi: {
    listMcpClients: (...args: unknown[]) => listMcpClients(...args),
    createMcpClient: (...args: unknown[]) => createMcpClient(...args),
    updateMcpClient: (...args: unknown[]) => updateMcpClient(...args),
    deleteMcpClient: (...args: unknown[]) => deleteMcpClient(...args),
    discoverMcpTools: (...args: unknown[]) => discoverMcpTools(...args),
    listMcpToolPolicies: (...args: unknown[]) => listMcpToolPolicies(...args),
    updateMcpToolPolicy: (...args: unknown[]) => updateMcpToolPolicy(...args),
  },
}));

const sampleClient = {
  id: 1,
  name: "Tiqora KB",
  url: "https://tiqora.example.com/mcp",
  has_auth_token: true,
  transport: "streamable_http",
  last_discovered_at: null,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AiMcpClientsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiMcpClientsPage", () => {
  beforeEach(() => {
    listMcpClients.mockReset();
    createMcpClient.mockReset();
    updateMcpClient.mockReset();
    deleteMcpClient.mockReset();
    discoverMcpTools.mockReset();
    listMcpToolPolicies.mockReset();
    updateMcpToolPolicy.mockReset();

    listMcpClients.mockResolvedValue({ items: [sampleClient], total: 1, page: 1, page_size: 1 });
    listMcpToolPolicies.mockResolvedValue([
      { id: 1, mcp_client_id: 1, tool_name: "kb_search", enabled: false, mutating: false, description_snapshot: "Search the KB" },
    ]);
  });

  it("runs discover and renders the resulting tool list", async () => {
    discoverMcpTools.mockResolvedValue({ tool_names: ["kb_search"], added: ["kb_search"], removed: [] });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-mcp-discover-1")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-ai-mcp-discover-1"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-mcp-discover-result-1").textContent).toMatch(/1/);
    });
    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-mcp-tools-1")).toBeInTheDocument();
    });
    expect(screen.getByText("kb_search")).toBeInTheDocument();
  });

  it("toggles a tool's enabled flag via PUT", async () => {
    updateMcpToolPolicy.mockResolvedValue({
      id: 1,
      mcp_client_id: 1,
      tool_name: "kb_search",
      enabled: true,
      mutating: false,
      description_snapshot: "Search the KB",
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-mcp-toggle-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-ai-mcp-toggle-1"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-mcp-tool-enabled-1-kb_search")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("admin-ai-mcp-tool-enabled-1-kb_search"));

    await waitFor(() => {
      expect(updateMcpToolPolicy).toHaveBeenCalledWith(1, "kb_search", { enabled: true });
    });
  });

  it("deletes a client only after confirming in the ConfirmDialog", async () => {
    deleteMcpClient.mockResolvedValue(undefined);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-mcp-delete-1")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-ai-mcp-delete-1"));
    await screen.findByTestId("confirm-dialog");
    expect(deleteMcpClient).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(deleteMcpClient).toHaveBeenCalledWith(1));
  });

  it("creates a client via the drawer", async () => {
    createMcpClient.mockResolvedValue({ ...sampleClient, id: 2, name: "New client" });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-mcp-new")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-ai-mcp-new"));
    fireEvent.change(screen.getByTestId("admin-ai-mcp-form-name"), { target: { value: "New client" } });
    fireEvent.change(screen.getByTestId("admin-ai-mcp-form-url"), {
      target: { value: "https://example.com/mcp" },
    });
    fireEvent.click(screen.getByTestId("admin-ai-mcp-form-submit"));

    await waitFor(() => {
      expect(createMcpClient).toHaveBeenCalledWith(
        expect.objectContaining({ name: "New client", url: "https://example.com/mcp" }),
      );
    });
  });
});
