import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AiQueuePoliciesPage } from "./AiQueuePoliciesPage";

const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

const listReferenceQueues = vi.fn();
const listReferenceAgents = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listReferenceQueues: (...args: unknown[]) => listReferenceQueues(...args),
    listReferenceAgents: (...args: unknown[]) => listReferenceAgents(...args),
  },
}));

const listQueuePolicies = vi.fn();
const deleteQueuePolicy = vi.fn();
const listProviders = vi.fn();
const listMcpClients = vi.fn();
const listUsage = vi.fn();

vi.mock("@/lib/aiApi", () => ({
  aiApi: {
    listQueuePolicies: (...args: unknown[]) => listQueuePolicies(...args),
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
    navigate.mockReset();
    listReferenceQueues.mockReset();
    listReferenceAgents.mockReset();
    listQueuePolicies.mockReset();
    deleteQueuePolicy.mockReset();
    listProviders.mockReset();
    listMcpClients.mockReset();
    listUsage.mockReset();

    listReferenceQueues.mockResolvedValue([
      { id: 10, name: "Support" },
      { id: 11, name: "Sales" },
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
      expect(screen.getByTestId("admin-ai-queues-table")).toHaveTextContent("Support");
    });
  });

  it("navigates to the editor when clicking a row's edit action", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-edit-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-row-edit-1"));

    expect(navigate).toHaveBeenCalledWith({
      to: "/admin/ai/queues/$policyId",
      params: { policyId: "1" },
    });
  });

  it("navigates to the new-policy route from the + button", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queues-new")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("admin-ai-queues-new"));

    expect(navigate).toHaveBeenCalledWith({ to: "/admin/ai/queues/new" });
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

  it("switches to the usage tab and loads usage rows", async () => {
    listUsage.mockResolvedValue({
      items: [
        {
          id: 1,
          ts: "2026-07-01T12:00:00Z",
          user_id: null,
          queue_id: 10,
          ticket_id: 5,
          feature: "summary",
          provider_id: null,
          model: "gpt-4.1",
          prompt_tokens: 100,
          completion_tokens: 50,
          cost_hint: null,
          success: true,
          error: null,
        },
      ],
      total: 1,
      total_prompt_tokens: 100,
      total_completion_tokens: 50,
      page: 1,
      page_size: 25,
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queues-table")).toBeInTheDocument());
    expect(listUsage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Usage"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-usage-table")).toHaveTextContent("gpt-4.1");
    });
    expect(screen.getByTestId("admin-ai-usage-totals").textContent).toMatch(/100/);
  });
});
