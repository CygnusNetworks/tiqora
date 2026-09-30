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
const duplicateProvider = vi.fn();
const listModels = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to,
    ...rest
  }: {
    children: React.ReactNode;
    to: string;
  } & Record<string, unknown>) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

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
    duplicateProvider: (...args: unknown[]) => duplicateProvider(...args),
    listModels: (...args: unknown[]) => listModels(...args),
  },
}));

const sampleProvider = {
  id: 1,
  name: "Nebius",
  kind: "openai_compat",
  base_url: "https://api.studio.nebius.ai",
  has_api_key: true,
  extra_json: null,
  eu_hosted: true,
  price_currency: "EUR",
  budget_cost_day: null,
  budget_cost_week: null,
  budget_cost_month: null,
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
    duplicateProvider.mockReset();
    listModels.mockReset();
    listModels.mockResolvedValue([
      { id: 10, provider_id: 1 },
      { id: 11, provider_id: 1 },
      { id: 12, provider_id: 2 },
    ]);

    listProviders.mockResolvedValue({
      items: [sampleProvider],
      total: 1,
      page: 1,
      page_size: 1,
    });
  });

  it("renders the provider list and never shows the api_key value", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Nebius")).toBeInTheDocument();
    });
    expect(screen.queryByText(/sk-/)).not.toBeInTheDocument();
  });

  it("creates a provider via the drawer", async () => {
    createProvider.mockResolvedValue({
      ...sampleProvider,
      id: 2,
      name: "OpenAI",
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-providers-new")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("admin-ai-providers-new"));
    fireEvent.change(screen.getByTestId("admin-ai-provider-form-name"), {
      target: { value: "OpenAI" },
    });
    fireEvent.change(screen.getByTestId("admin-ai-provider-form-base_url"), {
      target: { value: "https://api.openai.com/v1" },
    });
    fireEvent.click(screen.getByTestId("admin-ai-provider-form-submit"));

    await waitFor(() => {
      expect(createProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "OpenAI",
          kind: "openai_compat",
          base_url: "https://api.openai.com/v1",
        }),
      );
    });
    const body = createProvider.mock.calls[0][0] as Record<string, unknown>;
    // Model, capabilities, tool rounds and prices moved to the models page.
    for (const gone of [
      "default_model",
      "supports_tools",
      "supports_vision",
      "max_tool_rounds",
      "price_input_per_1m",
    ]) {
      expect(body).not.toHaveProperty(gone);
    }
  });

  it("submits provider cost-budget fields via the drawer", async () => {
    createProvider.mockResolvedValue({
      ...sampleProvider,
      id: 3,
      name: "Budgeted",
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-providers-new")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("admin-ai-providers-new"));
    fireEvent.change(screen.getByTestId("admin-ai-provider-form-name"), {
      target: { value: "Budgeted" },
    });
    fireEvent.change(screen.getByTestId("admin-ai-provider-form-base_url"), {
      target: { value: "https://api.example.com/v1" },
    });
    fireEvent.change(
      screen.getByTestId("admin-ai-provider-form-budget_cost_day"),
      { target: { value: "5" } },
    );
    fireEvent.change(
      screen.getByTestId("admin-ai-provider-form-budget_cost_week"),
      { target: { value: "25" } },
    );
    fireEvent.change(
      screen.getByTestId("admin-ai-provider-form-budget_cost_month"),
      { target: { value: "90" } },
    );
    fireEvent.click(screen.getByTestId("admin-ai-provider-form-submit"));

    await waitFor(() => {
      expect(createProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          budget_cost_day: 5,
          budget_cost_week: 25,
          budget_cost_month: 90,
        }),
      );
    });
  });

  it("deletes a provider via the ⋯-menu only after confirming", async () => {
    deleteProvider.mockResolvedValue(undefined);
    renderPage();
    await waitFor(() =>
      expect(
        screen.getByTestId("admin-ai-provider-menu-trigger-1"),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("admin-ai-provider-menu-trigger-1"));
    fireEvent.click(await screen.findByTestId("admin-row-delete-1"));
    await screen.findByTestId("confirm-dialog");
    expect(deleteProvider).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(deleteProvider).toHaveBeenCalledWith(1));
  });

  it("opens the edit drawer when the row is clicked", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-provider-row-1")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("admin-ai-provider-row-1"));
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-provider-form-name")).toHaveValue(
        "Nebius",
      ),
    );
  });

  it("shows the test result after testing via the ⋯-menu", async () => {
    testProvider.mockResolvedValue({ ok: true, detail: null, model_count: 42 });
    renderPage();
    await waitFor(() =>
      expect(
        screen.getByTestId("admin-ai-provider-menu-trigger-1"),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("admin-ai-provider-menu-trigger-1"));
    fireEvent.click(await screen.findByTestId("admin-ai-provider-test-1"));
    await waitFor(() => {
      expect(
        screen.getByTestId("admin-ai-provider-test-result-1").textContent,
      ).toMatch(/42/);
    });
  });

  it("duplicates a provider via the ⋯-menu and opens the edit dialog for the copy", async () => {
    const copy = { ...sampleProvider, id: 2, name: "Nebius (Kopie)" };
    duplicateProvider.mockResolvedValue(copy);
    listProviders.mockResolvedValue({
      items: [sampleProvider],
      total: 1,
      page: 1,
      page_size: 1,
    });
    renderPage();
    await waitFor(() =>
      expect(
        screen.getByTestId("admin-ai-provider-menu-trigger-1"),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("admin-ai-provider-menu-trigger-1"));
    fireEvent.click(await screen.findByTestId("admin-ai-provider-duplicate-1"));
    await waitFor(() => expect(duplicateProvider).toHaveBeenCalledWith(1));

    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-provider-form-name")).toHaveValue(
        "Nebius (Kopie)",
      ),
    );
  });

  it("offers only the OpenAI-compatible kind for a new provider", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("admin-ai-providers-new"));
    fireEvent.click(screen.getByTestId("admin-ai-provider-form-kind"));
    const menu = await screen.findByTestId("admin-ai-provider-form-kind-menu");
    expect(menu.querySelectorAll('[role="option"]')).toHaveLength(1);
    expect(
      screen.getByTestId("admin-ai-provider-form-kind-menu-option-openai_compat"),
    ).toBeInTheDocument();
  });

  it("links the provider's model count to the models page", async () => {
    renderPage();
    const link = await screen.findByTestId("admin-ai-provider-models-1");
    await waitFor(() => expect(link.textContent).toMatch(/2/));
    expect(link.getAttribute("href")).toBe("/admin/ai/models");
  });

  it("shows the server message when deleting is refused (409)", async () => {
    const { ApiError } = await import("@/lib/api");
    deleteProvider.mockRejectedValue(
      new ApiError(
        409,
        "Modelle dieses Providers werden in Profil(en) „Agent“ verwendet.",
        "/x",
      ),
    );
    renderPage();
    fireEvent.click(
      await screen.findByTestId("admin-ai-provider-menu-trigger-1"),
    );
    fireEvent.click(await screen.findByTestId("admin-row-delete-1"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect(
      (await screen.findByTestId("admin-ai-providers-action-error")).textContent,
    ).toMatch(/Profil\(en\) „Agent“/);
  });
});
