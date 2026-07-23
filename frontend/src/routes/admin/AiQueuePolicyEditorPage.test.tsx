import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { AiQueuePolicyNewPage, AiQueuePolicyEditPage } from "./AiQueuePolicyEditorPage";

const navigate = vi.fn();
let currentPolicyId = "1";

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useParams: () => ({ policyId: currentPolicyId }),
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

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
const listProviders = vi.fn();
const listMcpClients = vi.fn();
const getSettings = vi.fn();

vi.mock("@/lib/aiApi", () => ({
  aiApi: {
    listQueuePolicies: (...args: unknown[]) => listQueuePolicies(...args),
    createQueuePolicy: (...args: unknown[]) => createQueuePolicy(...args),
    updateQueuePolicy: (...args: unknown[]) => updateQueuePolicy(...args),
    listProviders: (...args: unknown[]) => listProviders(...args),
    listMcpClients: (...args: unknown[]) => listMcpClients(...args),
    getSettings: (...args: unknown[]) => getSettings(...args),
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
  ignored_senders: null,
  ignore_senders_manual: false,
  reply_language_mode: "off",
  reply_language_fixed: null,
  reply_language_default: null,
  allowed_state_types: null,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

function renderEdit() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AiQueuePolicyEditPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

function renderNew() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AiQueuePolicyNewPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiQueuePolicyEditorPage", () => {
  beforeEach(() => {
    currentPolicyId = "1";
    navigate.mockReset();
    listReferenceQueues.mockReset();
    listReferenceAgents.mockReset();
    listQueuePolicies.mockReset();
    createQueuePolicy.mockReset();
    updateQueuePolicy.mockReset();
    listProviders.mockReset();
    listMcpClients.mockReset();
    getSettings.mockReset();

    listReferenceQueues.mockResolvedValue([
      { id: 10, name: "Support" },
      { id: 11, name: "Sales" },
    ]);
    listReferenceAgents.mockResolvedValue([{ id: 1, login: "agent1", full_name: "Agent One" }]);
    listQueuePolicies.mockResolvedValue({ items: [samplePolicy], total: 1, page: 1, page_size: 1 });
    listProviders.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 0 });
    listMcpClients.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 0 });
    getSettings.mockResolvedValue({
      operation_mode: "tiqora_primary",
      disclosure_default_text: "",
      global_max_replies_per_hour: null,
    });
  });

  it("loads the existing policy and switches tabs", async () => {
    renderEdit();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toBeInTheDocument());
    expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toHaveValue("Be helpful.");

    fireEvent.click(screen.getByText("Drafts"));
    expect(screen.getByTestId("admin-ai-queue-form-enabled_manual_assist")).toBeChecked();

    fireEvent.click(screen.getByText("Summaries"));
    expect(screen.getByTestId("admin-ai-queue-form-enabled_summary")).toBeChecked();
  });

  it("shows a green dot on tabs whose feature is enabled", async () => {
    renderEdit();
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-tab-dot-drafts")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("admin-ai-queue-tab-dot-summaries")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-ai-queue-tab-dot-auto")).not.toBeInTheDocument();
  });

  it("disables the drafts tab's KB fields while manual assist is off", async () => {
    renderEdit();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Drafts"));

    // Sample policy has manual assist ON — fields are enabled; switch it off to verify gating.
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-enabled_manual_assist"));
    // kb_tags is a TagInput now — the fieldset's disabled state lands on its
    // inner text input, not the wrapper div.
    expect(screen.getByTestId("admin-ai-queue-form-kb_tags-input")).toBeDisabled();
  });

  it("opens the create page pre-filled with defaults, incl. the sole provider", async () => {
    currentPolicyId = "";
    listProviders.mockResolvedValue({
      items: [{ id: 7, name: "Nebius" }],
      total: 1,
      page: 1,
      page_size: 1,
    });
    renderNew();

    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-form-queue_id")).toBeInTheDocument(),
    );
    // Support (#10) already has a policy — the first *available* queue is Sales (#11).
    expect(screen.getByTestId("admin-ai-queue-form-queue_id")).toHaveTextContent("Sales");

    fireEvent.click(screen.getByText("Auto replies"));
    expect(screen.getByTestId("admin-ai-queue-form-max_replies_per_hour")).toHaveValue(20);
    expect(screen.getByTestId("admin-ai-queue-form-budget_tokens_day")).toHaveValue(500000);

    fireEvent.click(screen.getByText("Basics"));
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-form-llm_provider_id")).toHaveTextContent("Nebius"),
    );
  });

  it("creates a new policy with the queue and defaults on save", async () => {
    currentPolicyId = "";
    createQueuePolicy.mockResolvedValue({ ...samplePolicy, id: 2, queue_id: 11 });
    renderNew();

    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-queue-form-queue_id")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("admin-ai-queue-editor-save"));

    await waitFor(() => {
      expect(createQueuePolicy).toHaveBeenCalledWith(
        expect.objectContaining({ queue_id: 11, autonomy: "off", max_replies_per_hour: 20 }),
      );
    });
  });

  it("saves edits via PUT", async () => {
    updateQueuePolicy.mockResolvedValue({ ...samplePolicy, system_prompt: "New prompt" });
    renderEdit();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("admin-ai-queue-form-system_prompt"), {
      target: { value: "New prompt" },
    });
    fireEvent.click(screen.getByTestId("admin-ai-queue-editor-save"));

    await waitFor(() => {
      expect(updateQueuePolicy).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ system_prompt: "New prompt" }),
      );
    });
  });

  it("maps a 409 gate error to a friendly message", async () => {
    updateQueuePolicy.mockRejectedValue(new ApiError(409, "gate closed", "/admin/ai/queue-policies/1"));
    renderEdit();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-editor-save")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-ai-queue-editor-save"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-queue-editor-error").textContent).toMatch(/tiqora_primary/);
    });
  });

  it("locks the auto-reply toggle and shows a warning while operation_mode is parallel", async () => {
    getSettings.mockResolvedValue({
      operation_mode: "parallel",
      disclosure_default_text: "",
      global_max_replies_per_hour: null,
    });
    renderEdit();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Auto replies"));

    expect(screen.getByTestId("admin-ai-queue-auto-gate-warning")).toBeInTheDocument();
    // Sample policy has enabled_auto_reply=false — the switch stays locked off.
    expect(screen.getByTestId("admin-ai-queue-form-enabled_auto_reply")).toBeDisabled();
  });

  it("still allows turning an already-enabled auto-reply switch back off while parallel", async () => {
    listQueuePolicies.mockResolvedValue({
      items: [{ ...samplePolicy, enabled_auto_reply: true, autonomy: "full", service_user_id: 1 }],
      total: 1,
      page: 1,
      page_size: 1,
    });
    getSettings.mockResolvedValue({
      operation_mode: "parallel",
      disclosure_default_text: "",
      global_max_replies_per_hour: null,
    });
    renderEdit();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Auto replies"));

    expect(screen.getByTestId("admin-ai-queue-form-enabled_auto_reply")).not.toBeDisabled();
    expect(screen.getByTestId("admin-ai-queue-form-enabled_auto_reply")).toBeChecked();
  });

  it("renders a character counter for the system prompt", async () => {
    renderEdit();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toBeInTheDocument());
    // samplePolicy.system_prompt = "Be helpful." (11 characters)
    expect(screen.getByTestId("admin-ai-queue-form-prompt-char-count").textContent).toMatch(/11/);
    expect(screen.queryByTestId("admin-ai-queue-form-prompt-char-warning")).not.toBeInTheDocument();

    fireEvent.change(screen.getByTestId("admin-ai-queue-form-system_prompt"), {
      target: { value: "x".repeat(100_000) },
    });
    expect(screen.getByTestId("admin-ai-queue-form-prompt-char-warning")).toBeInTheDocument();
  });

  it("loads a file into the system prompt, replacing existing content after confirm", async () => {
    renderEdit();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toBeInTheDocument());
    expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toHaveValue("Be helpful.");

    const file = new File(["Loaded prompt from file."], "prompt.txt", { type: "text/plain" });
    fireEvent.change(screen.getByTestId("admin-ai-queue-form-prompt-file-input"), {
      target: { files: [file] },
    });

    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toHaveValue(
        "Loaded prompt from file.",
      );
    });
  });

  it("selects two MCP clients and includes both IDs in the save body", async () => {
    listMcpClients.mockResolvedValue({
      items: [
        { id: 21, name: "Tiqora KB", url: "https://kb.example.com/mcp" },
        { id: 22, name: "Netadmin", url: "https://netadmin.example.com/mcp" },
      ],
      total: 2,
      page: 1,
      page_size: 2,
    });
    updateQueuePolicy.mockResolvedValue(samplePolicy);
    renderEdit();
    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-system_prompt")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Drafts"));

    await waitFor(() => expect(screen.getByTestId("admin-ai-queue-form-mcp-21")).toBeInTheDocument());
    expect(screen.getByTestId("admin-ai-queue-form-mcp-selected-count").textContent).toMatch(/0/);

    fireEvent.click(screen.getByTestId("admin-ai-queue-form-mcp-21"));
    fireEvent.click(screen.getByTestId("admin-ai-queue-form-mcp-22"));
    expect(screen.getByTestId("admin-ai-queue-form-mcp-selected-count").textContent).toMatch(/2/);

    fireEvent.click(screen.getByTestId("admin-ai-queue-editor-save"));

    await waitFor(() => {
      expect(updateQueuePolicy).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ mcp_client_ids: "21,22" }),
      );
    });
  });
});
