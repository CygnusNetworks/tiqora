import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AiProvidersPage } from "./AiProvidersPage";

const listProviders = vi.fn();
const createProvider = vi.fn();
const updateProvider = vi.fn();
const deleteProvider = vi.fn();
const testProvider = vi.fn();

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
    listProviders: (...args: unknown[]) => listProviders(...args),
    createProvider: (...args: unknown[]) => createProvider(...args),
    updateProvider: (...args: unknown[]) => updateProvider(...args),
    deleteProvider: (...args: unknown[]) => deleteProvider(...args),
    testProvider: (...args: unknown[]) => testProvider(...args),
  },
}));

const sampleProvider = {
  id: 1,
  name: "Nebius",
  kind: "openai_compat",
  base_url: "https://api.studio.nebius.ai",
  default_model: "llama-3.3-70b",
  has_api_key: true,
  extra_json: null,
  supports_tools: true,
  supports_streaming: true,
  eu_hosted: true,
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
        <AiProvidersPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiProvidersPage", () => {
  beforeEach(() => {
    listProviders.mockReset();
    createProvider.mockReset();
    updateProvider.mockReset();
    deleteProvider.mockReset();
    testProvider.mockReset();

    listProviders.mockResolvedValue({ items: [sampleProvider], total: 1, page: 1, page_size: 1 });
  });

  it("renders the provider list and never shows the api_key value", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Nebius")).toBeInTheDocument();
    });
    expect(screen.queryByText(/sk-/)).not.toBeInTheDocument();
  });

  it("creates a provider via the drawer", async () => {
    createProvider.mockResolvedValue({ ...sampleProvider, id: 2, name: "OpenAI" });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-providers-new")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-ai-providers-new"));
    fireEvent.change(screen.getByTestId("admin-ai-provider-form-name"), {
      target: { value: "OpenAI" },
    });
    fireEvent.change(screen.getByTestId("admin-ai-provider-form-base_url"), {
      target: { value: "https://api.openai.com/v1" },
    });
    fireEvent.change(screen.getByTestId("admin-ai-provider-form-default_model"), {
      target: { value: "gpt-4.1" },
    });
    fireEvent.click(screen.getByTestId("admin-ai-provider-form-submit"));

    await waitFor(() => {
      expect(createProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "OpenAI",
          base_url: "https://api.openai.com/v1",
          default_model: "gpt-4.1",
        }),
      );
    });
  });

  it("deletes a provider only after confirming in the ConfirmDialog", async () => {
    deleteProvider.mockResolvedValue(undefined);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-delete-1")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-row-delete-1"));
    await screen.findByTestId("confirm-dialog");
    expect(deleteProvider).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(deleteProvider).toHaveBeenCalledWith(1));
  });

  it("shows the test result after clicking the test button", async () => {
    testProvider.mockResolvedValue({ ok: true, model: "llama-3.3-70b", tool_calling_ok: true, error: null });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-provider-test-1")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-ai-provider-test-1"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-provider-test-result-1").textContent).toMatch(/llama-3.3-70b/);
    });
  });
});
