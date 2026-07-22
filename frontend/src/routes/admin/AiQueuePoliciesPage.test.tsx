import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { AiQueuePoliciesPage } from "./AiQueuePoliciesPage";

const listReferenceQueues = vi.fn();
const listReferenceAgents = vi.fn();

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
    listReferenceQueues: (...args: unknown[]) => listReferenceQueues(...args),
    listReferenceAgents: (...args: unknown[]) => listReferenceAgents(...args),
  },
}));

const listQueuePolicies = vi.fn();
const createQueuePolicy = vi.fn();
const updateQueuePolicy = vi.fn();
const deleteQueuePolicy = vi.fn();
const listProviders = vi.fn();
const listMcpClients = vi.fn();
const listUsage = vi.fn();

vi.mock("@/lib/aiApi", () => ({
  aiApi: {
    listQueuePolicies: (...args: unknown[]) => listQueuePolicies(...args),
    createQueuePolicy: (...args: unknown[]) => createQueuePolicy(...args),
    updateQueuePolicy: (...args: unknown[]) => updateQueuePolicy(...args),
    deleteQueuePolicy: (...args: unknown[]) => deleteQueuePolicy(...args),
    listProviders: (...args: unknown[]) => listProviders(...args),
    listMcpClients: (...args: unknown[]) => listMcpClients(...args),
    listUsage: (...args: unknown[]) => listUsage(...args),
  },
}));

const samplePolicy = {
  id: 1,
  queue_id: 10,
  enabled_auto_reply: false,
  enabled_summary: true,
  enabled_manual_assist: true,
  system_prompt: "Be helpful.",
  autonomy: "clarify_only",
  service_user_id: null,
  llm_provider_id: null,
  model_override: null,
  kb_tags: null,
  kb_category_ids: null,
  mcp_client_ids: null,
  mcp_tool_overrides: null,
  summary_article_threshold: null,
  summary_char_threshold: null,
  summary_incremental_min_articles: null,
  summary_incremental_min_chars: null,
  max_clarifications: 2,
  max_auto_replies: 5,
  max_replies_per_hour: null,
  budget_tokens_day: null,
  escalation_rules: null,
  ai_disclosure_enabled: false,
  ai_disclosure_text: null,
  pii_masking: true,
  identity_mode: "ticket_customer_id",
  clarify_schema_json: null,
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
        <AiQueuePoliciesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiQueuePoliciesPage", () => {
  beforeEach(() => {
    listReferenceQueues.mockReset();
    listReferenceAgents.mockReset();
    listQueuePolicies.mockReset();
    createQueuePolicy.mockReset();
    updateQueuePolicy.mockReset();
    deleteQueuePolicy.mockReset();
    listProviders.mockReset();
    listMcpClients.mockReset();
    listUsage.mockReset();

    listReferenceQueues.mockResolvedValue([
      { id: 10, name: "Support" },
      { id: 11, name: "Sales" },
      { id: 12, name: "Billing" },
    ]);
    listReferenceAgents.mockResolvedValue([{ id: 1, login: "agent1", full_name: "Agent One" }]);
    listQueuePolicies.mockResolvedValue({ items: [samplePolicy], total: 1, page: 1, page_size: 1 });
    listProviders.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 0 });
    listMcpClients.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 0 });
    listUsage.mockResolvedValue({
      items: [],
      total: 0,
      total_prompt_tokens: 0,
      total_completion_tokens: 0,
      page: 1,
      page_size: 25,
    });
  });

  it("renders existing policies resolved against queue names", async () => {
    renderPage();
    await waitFor(() => {
      const table = screen.getByTestId("admin-ai-queues-table");
      expect(table).toHaveTextContent("Support");
    });
  });

  it("deletes a policy only after confirming in the ConfirmDialog", async () => {
    deleteQueuePolicy.mockResolvedValue(undefined);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-delete-1")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-row-delete-1"));
    await screen.findByTestId("confirm-dialog");
    expect(deleteQueuePolicy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(deleteQueuePolicy).toHaveBeenCalledWith(1));
  });

  it("shows a friendly gate message on a 409 from the backend", async () => {
    updateQueuePolicy.mockRejectedValue(new ApiError(409, "gate closed", "/admin/ai/queue-policies/1"));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-edit-1")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-row-edit-1"));
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-submit")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-queue-form-error").textContent).toMatch(
        /tiqora_primary/,
      );
    });
  });

  it("changes autonomy and provider via the SelectMenu pickers and PUTs the new values", async () => {
    listProviders.mockResolvedValue({
      items: [{ id: 5, name: "Nebius" }],
      total: 1,
      page: 1,
      page_size: 1,
    });
    updateQueuePolicy.mockResolvedValue({ ...samplePolicy, autonomy: "full", llm_provider_id: 5 });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-edit-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-row-edit-1"));

    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-autonomy")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-autonomy"));
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-autonomy-panel-option-full"));

    fireEvent.click(screen.getByTestId("admin-ai-queue-form-llm_provider_id"));
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-form-llm_provider_id-panel-option-5")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-llm_provider_id-panel-option-5"));

    fireEvent.click(screen.getByTestId("admin-ai-queue-form-submit"));

    await waitFor(() => {
      expect(updateQueuePolicy).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ autonomy: "full", llm_provider_id: 5 }),
      );
    });
  });

  it("blocks submit on invalid escalation_rules JSON without calling the API", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-edit-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-row-edit-1"));

    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-form-escalation_rules")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByTestId("admin-ai-queue-form-escalation_rules"), {
      target: { value: "{not valid json" },
    });
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-queue-form-escalation_rules-error")).toBeInTheDocument();
    });
    expect(updateQueuePolicy).not.toHaveBeenCalled();
  });

  it("opens the create dialog pre-filled with sensible defaults, incl. the sole provider", async () => {
    listProviders.mockResolvedValue({
      items: [{ id: 7, name: "Nebius" }],
      total: 1,
      page: 1,
      page_size: 1,
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queues-new")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("admin-ai-queues-new"));

    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-form-autonomy")).toHaveTextContent("Off"),
    );
    expect(screen.getByTestId("admin-ai-queue-form-identity_mode")).toHaveTextContent(
      "Ticket customer",
    );
    expect(screen.getByTestId("admin-ai-queue-form-pii_masking")).toBeChecked();
    expect(screen.getByTestId("admin-ai-queue-form-enabled_auto_reply")).not.toBeChecked();
    expect(screen.getByTestId("admin-ai-queue-form-enabled_summary")).not.toBeChecked();
    expect(screen.getByTestId("admin-ai-queue-form-enabled_manual_assist")).not.toBeChecked();
    expect(screen.getByTestId("admin-ai-queue-form-ai_disclosure_enabled")).not.toBeChecked();
    expect(screen.getByTestId("admin-ai-queue-form-max_clarifications")).toHaveValue(2);
    expect(screen.getByTestId("admin-ai-queue-form-max_auto_replies")).toHaveValue(5);
    expect(screen.getByTestId("admin-ai-queue-form-max_replies_per_hour")).toHaveValue(20);
    expect(screen.getByTestId("admin-ai-queue-form-budget_tokens_day")).toHaveValue(500000);
    expect(screen.getByTestId("admin-ai-queue-form-summary_article_threshold")).toHaveValue(10);
    expect(screen.getByTestId("admin-ai-queue-form-summary_char_threshold")).toHaveValue(20000);
    expect(
      screen.getByTestId("admin-ai-queue-form-summary_incremental_min_articles"),
    ).toHaveValue(2);
    expect(screen.getByTestId("admin-ai-queue-form-summary_incremental_min_chars")).toHaveValue(
      2000,
    );
    // The sole configured provider is preselected.
    expect(screen.getByTestId("admin-ai-queue-form-llm_provider_id")).toHaveTextContent("Nebius");
  });

  it("leaves the provider unset in the create dialog when more than one exists", async () => {
    listProviders.mockResolvedValue({
      items: [
        { id: 7, name: "Nebius" },
        { id: 8, name: "OpenAI" },
      ],
      total: 2,
      page: 1,
      page_size: 2,
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queues-new")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("admin-ai-queues-new"));

    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-form-llm_provider_id")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("admin-ai-queue-form-llm_provider_id")).not.toHaveTextContent(
      "Nebius",
    );
  });

  it("creates a policy for a queue without one, picked via the SelectMenu", async () => {
    createQueuePolicy.mockResolvedValue({ ...samplePolicy, id: 2, queue_id: 12 });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queues-table")).toHaveTextContent("Support"),
    );
    await waitFor(() => expect(screen.getByTestId("admin-ai-queues-new")).not.toBeDisabled());

    fireEvent.click(screen.getByTestId("admin-ai-queues-new"));
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-queue_id")).toBeInTheDocument());

    // Default selection is the first available queue (Sales, #11) — explicitly
    // pick Billing (#12) via the SelectMenu to exercise the picker itself.
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-queue_id"));
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-queue_id-panel-option-12"));
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-submit"));

    await waitFor(() => {
      expect(createQueuePolicy).toHaveBeenCalledWith(expect.objectContaining({ queue_id: 12 }));
    });
  });
});
