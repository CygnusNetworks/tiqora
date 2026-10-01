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
const listProfiles = vi.fn();
const getTaskDefaults = vi.fn();
const listMcpClients = vi.fn();
const listUsage = vi.fn();
const listLimits = vi.fn();

vi.mock("@/lib/aiApi", () => ({
  aiApi: {
    listQueuePolicies: (...args: unknown[]) => listQueuePolicies(...args),
    deleteQueuePolicy: (...args: unknown[]) => deleteQueuePolicy(...args),
    listProfiles: (...args: unknown[]) => listProfiles(...args),
    getTaskDefaults: (...args: unknown[]) => getTaskDefaults(...args),
    listMcpClients: (...args: unknown[]) => listMcpClients(...args),
    listUsage: (...args: unknown[]) => listUsage(...args),
    listLimits: (...args: unknown[]) => listLimits(...args),
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
  enabled_refine: false,
  enabled_triage: false,
  task_profiles: [] as { task: string; profile_id: number | null }[],
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
    listProfiles.mockReset();
    getTaskDefaults.mockReset();
    listMcpClients.mockReset();
    listUsage.mockReset();
    listLimits.mockReset();
    listLimits.mockResolvedValue({ items: [] });

    listReferenceQueues.mockResolvedValue([
      { id: 10, name: "Support" },
      { id: 11, name: "Sales" },
    ]);
    listReferenceAgents.mockResolvedValue([{ id: 1, login: "agent1", full_name: "Agent One" }]);
    listQueuePolicies.mockResolvedValue({ items: [samplePolicy], total: 1, page: 1, page_size: 1 });
    listProfiles.mockResolvedValue([
      { id: 10, name: "Agent", valid_id: 1, entries: [] },
      { id: 12, name: "Schnell", valid_id: 1, entries: [] },
      { id: 13, name: "Alt", valid_id: 2, entries: [] },
    ]);
    getTaskDefaults.mockResolvedValue([
      { task: "agent", profile_id: 10 },
      { task: "final_answer", profile_id: null },
      { task: "triage", profile_id: null },
      { task: "summary", profile_id: null },
      { task: "refine", profile_id: null },
      { task: "vision", profile_id: null },
    ]);
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

  it("shows today's spend against the daily token budget", async () => {
    const budget = {
      kind: "queue_tokens_day",
      subject_id: 10,
      subject_name: "Support",
      window: "day",
      used: 3_250_000,
      limit: 3_000_000,
      currency: null,
      exhausted: true,
      window_start: "2026-10-01T00:00:00Z",
      resets_at: "2026-10-02T00:00:00Z",
    };
    listLimits.mockResolvedValue({ items: [budget] });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-budget-1")).toHaveTextContent(
        i18n.t("admin.ai.queues.list.budgetExhausted", { amount: "3.25 / 3" }),
      ),
    );
  });

  it("shows the profile “Recherche und Werkzeuge” runs on in each queue", async () => {
    listQueuePolicies.mockResolvedValue({
      items: [
        samplePolicy,
        { ...samplePolicy, id: 2, queue_id: 11, task_profiles: [{ task: "agent", profile_id: 12 }] },
        { ...samplePolicy, id: 3, queue_id: 12, task_profiles: [{ task: "agent", profile_id: null }] },
        // A disabled profile counts as none.
        { ...samplePolicy, id: 4, queue_id: 13, task_profiles: [{ task: "agent", profile_id: 13 }] },
      ],
      total: 4,
      page: 1,
      page_size: 4,
    });
    renderPage();
    const none = i18n.t("admin.ai.queues.list.noAgentProfile", {
      fallback: i18n.t("admin.ai.tasks.fallback.agent"),
    });
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-agent-profile-1")).toHaveTextContent(
        i18n.t("admin.ai.queues.list.agentProfile", { name: "Agent" }),
      ),
    );
    expect(screen.getByTestId("admin-ai-queue-agent-profile-2")).toHaveTextContent(
      i18n.t("admin.ai.queues.list.agentProfile", { name: "Schnell" }),
    );
    expect(screen.getByTestId("admin-ai-queue-agent-profile-3")).toHaveTextContent(none);
    expect(screen.getByTestId("admin-ai-queue-agent-profile-4")).toHaveTextContent(none);
  });

  it("names the error behind the ⚠ marker when the profiles cannot be loaded", async () => {
    listProfiles.mockRejectedValue(new Error("boom"));
    renderPage();
    const marker = await screen.findByRole("img", {
      name: i18n.t("admin.ai.queues.list.agentProfileError"),
    });
    expect(marker).toHaveAttribute("title", i18n.t("admin.ai.queues.list.agentProfileError"));
    expect(screen.getByTestId("admin-ai-queue-agent-profile-1")).toContainElement(marker);
  });

  it("navigates to the editor from the row's ⋯ edit action", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-menu-trigger-1")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("admin-ai-queue-menu-trigger-1"));
    fireEvent.click(await screen.findByTestId("admin-ai-queue-edit-1"));

    expect(navigate).toHaveBeenCalledWith({
      to: "/admin/ai/queues/$policyId",
      params: { policyId: "1" },
    });
  });

  it("shows a triage chip for a triage-only policy", async () => {
    listQueuePolicies.mockResolvedValue({
      items: [
        {
          ...samplePolicy,
          enabled_summary: false,
          enabled_manual_assist: false,
          enabled_triage: true,
        },
      ],
      total: 1,
      page: 1,
      page_size: 1,
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-queue-row-1")).toHaveTextContent("Triage");
    });
    expect(screen.queryByText("none")).not.toBeInTheDocument();
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
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-menu-trigger-1")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("admin-ai-queue-menu-trigger-1"));
    fireEvent.click(await screen.findByTestId("admin-row-delete-1"));
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
