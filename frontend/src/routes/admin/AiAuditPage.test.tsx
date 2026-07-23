import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AiAuditPage } from "./AiAuditPage";

const listAuditLog = vi.fn();
const getAuditLogStats = vi.fn();
const listProviders = vi.fn();
const getAuditLogEntry = vi.fn();
const revealAuditPii = vi.fn();
const getSettings = vi.fn();
const putSettings = vi.fn();

vi.mock("@/lib/aiApi", () => ({
  aiApi: {
    listAuditLog: (...args: unknown[]) => listAuditLog(...args),
    getAuditLogStats: (...args: unknown[]) => getAuditLogStats(...args),
    listProviders: (...args: unknown[]) => listProviders(...args),
    getAuditLogEntry: (...args: unknown[]) => getAuditLogEntry(...args),
    revealAuditPii: (...args: unknown[]) => revealAuditPii(...args),
    getSettings: (...args: unknown[]) => getSettings(...args),
    putSettings: (...args: unknown[]) => putSettings(...args),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    ...rest
  }: {
    children: React.ReactNode;
    to?: string;
    params?: Record<string, string>;
  }) => (
    <a href="#" {...rest}>
      {children}
    </a>
  ),
}));

const sampleItems = [
  {
    id: 501,
    ts: "2026-07-23T10:00:00",
    run_id: "run-abc",
    provider_id: 1,
    provider_name: "OpenAI EU",
    model: "gpt-x",
    feature: "draft" as const,
    ticket_id: 4200,
    queue_id: 3,
    acting_user_id: 9,
    trigger: "manual",
    status_code: 200,
    error: null,
    duration_ms: 842,
    prompt_tokens: 120,
    completion_tokens: 40,
    pii_counts: { EMAIL: 2, IPV4: 1 },
  },
  {
    id: 502,
    ts: "2026-07-23T11:00:00",
    run_id: "run-def",
    provider_id: 1,
    provider_name: "OpenAI EU",
    model: "gpt-x",
    feature: "auto_reply" as const,
    ticket_id: null,
    queue_id: 3,
    acting_user_id: null,
    trigger: "auto",
    status_code: null,
    error: "HTTP 500: boom",
    duration_ms: 55,
    prompt_tokens: null,
    completion_tokens: null,
    pii_counts: null,
  },
];

const sampleDetail = {
  ...sampleItems[0],
  request_json: JSON.stringify({
    messages: [
      { role: "system", content: "You are a helpful agent." },
      { role: "user", content: "Contact me at [EMAIL_1] please" },
    ],
    max_tokens: 512,
    temperature: 0.2,
  }),
  response_json: JSON.stringify({
    content: "Sure, I will help.",
    tool_calls: [],
    finish_reason: "stop",
    model: "gpt-x",
  }),
};

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AiAuditPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiAuditPage", () => {
  beforeEach(() => {
    listAuditLog.mockReset();
    getAuditLogStats.mockReset();
    listProviders.mockReset();
    getAuditLogEntry.mockReset();
    revealAuditPii.mockReset();
    getSettings.mockReset();
    putSettings.mockReset();

    listAuditLog.mockResolvedValue({ items: sampleItems, total: 2, page: 1, page_size: 25 });
    getAuditLogStats.mockResolvedValue({
      total_requests: 2,
      total_prompt_tokens: 120,
      total_completion_tokens: 40,
      error_rate: 0.5,
      per_day: [{ date: "2026-07-23", count: 2 }],
      top_model: "gpt-x",
    });
    listProviders.mockResolvedValue({
      items: [{ id: 1, name: "OpenAI EU" }],
      total: 1,
      page: 1,
      page_size: 1,
    });
    getAuditLogEntry.mockResolvedValue(sampleDetail);
    revealAuditPii.mockResolvedValue({ mapping: { "[EMAIL_1]": "real@example.com" } });
    getSettings.mockResolvedValue({
      operation_mode: "tiqora_primary",
      disclosure_default_text: "",
      global_max_replies_per_hour: null,
      audit_retention_days: 30,
    });
    putSettings.mockResolvedValue({
      operation_mode: "tiqora_primary",
      disclosure_default_text: "",
      global_max_replies_per_hour: null,
      audit_retention_days: 45,
    });
  });

  it("renders KPI cards, chart and table rows", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByTestId("ai-audit-table")).toBeInTheDocument());

    expect(screen.getByTestId("ai-audit-stat-requests")).toHaveTextContent("2");
    expect(screen.getByTestId("ai-audit-stat-error-rate")).toHaveTextContent("50.0%");
    expect(screen.getByTestId("ai-audit-stat-top-model")).toHaveTextContent("gpt-x");
    expect(screen.getByTestId("ai-audit-chart")).toBeInTheDocument();
    expect(screen.getByTestId("ai-audit-row-501")).toBeInTheDocument();
    expect(screen.getByTestId("ai-audit-row-502")).toBeInTheDocument();
    expect(screen.getByTestId("ai-audit-row-501-pii")).toHaveTextContent("3");
  });

  it("passes the feature filter to listAuditLog", async () => {
    renderPage();
    await waitFor(() => expect(listAuditLog).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("ai-audit-filter-feature"));
    fireEvent.click(await screen.findByTestId("ai-audit-filter-feature-panel-option-auto_reply"));

    await waitFor(() => {
      const last = listAuditLog.mock.calls.at(-1)?.[0] as { feature?: string };
      expect(last?.feature).toBe("auto_reply");
    });
  });

  it("opens the detail drawer, shows messages, and reveals PII on toggle", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("ai-audit-row-501")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("ai-audit-row-501"));

    await waitFor(() => expect(screen.getByTestId("ai-audit-drawer")).toBeInTheDocument());
    expect(getAuditLogEntry).toHaveBeenCalledWith(501, expect.anything());
    await waitFor(() => expect(screen.getByTestId("ai-audit-messages-tab")).toBeInTheDocument());
    expect(screen.getByText(/You are a helpful agent/)).toBeInTheDocument();
    expect(screen.getByText(/\[EMAIL_1\]/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("ai-audit-pii-toggle"));
    await waitFor(() => expect(revealAuditPii).toHaveBeenCalledWith(501));
    await waitFor(() => expect(screen.getByText(/real@example\.com/)).toBeInTheDocument());
  });

  it("switches to the raw JSON tab and shows pretty-printed payloads", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("ai-audit-row-501"));
    await waitFor(() => expect(screen.getByTestId("ai-audit-messages-tab")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("tab", { name: /Raw JSON|Roh-JSON/ }));

    await waitFor(() => expect(screen.getByTestId("ai-audit-raw-request")).toBeInTheDocument());
    expect(screen.getByTestId("ai-audit-raw-request")).toHaveTextContent("max_tokens");
    expect(screen.getByTestId("ai-audit-raw-response")).toHaveTextContent("finish_reason");
  });

  it("edits and saves the retention setting", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("ai-audit-retention")).toHaveTextContent("30"),
    );

    fireEvent.click(screen.getByTestId("ai-audit-retention-edit"));
    const input = screen.getByTestId("ai-audit-retention-input");
    fireEvent.change(input, { target: { value: "45" } });
    fireEvent.click(screen.getByTestId("ai-audit-retention-save"));

    await waitFor(() => expect(putSettings).toHaveBeenCalledWith({ audit_retention_days: 45 }));
  });
});
