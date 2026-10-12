import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { useState, type ReactNode } from "react";
import i18n from "@/i18n";
import { AiTab } from "./AiTab";
import { newDraft, validateDraft, type Draft } from "@/lib/webchatForm";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, ...rest }: { children: ReactNode; to: string; [k: string]: unknown }) => (
    <a href={to} data-testid={rest["data-testid"] as string}>
      {children}
    </a>
  ),
}));

const aiStatus = vi.fn();
vi.mock("@/lib/webchatApi", async (orig) => {
  const mod = await orig<typeof import("@/lib/webchatApi")>();
  return { ...mod, webchatApi: { aiStatus: (...a: unknown[]) => aiStatus(...a) } };
});

function twoTopics(): Draft {
  const d = newDraft();
  d.name = "Website";
  d.values.topics = [
    { key: "support", label: "Support", queue_id: 1, priority_id: null, ai_mode: "off" },
    { key: "billing", label: "Rechnung", queue_id: 2, priority_id: null, ai_mode: "off" },
  ];
  return d;
}

let lastDraft: Draft = newDraft();
function Harness({ initial, configId, extra }: { initial: Draft; configId: number | null; extra?: Record<string, string> }) {
  const [draft, setDraft] = useState(initial);
  lastDraft = draft;
  const errors = { ...validateDraft(draft), ...extra };
  return <AiTab draft={draft} errors={errors} onDraft={setDraft} configId={configId} />;
}

function renderTab(initial: Draft, configId: number | null = 7, extra?: Record<string, string>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <Harness initial={initial} configId={configId} extra={extra} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiTab", () => {
  beforeAll(() => i18n.changeLanguage("de"));
  afterAll(() => i18n.changeLanguage("en"));
  beforeEach(() => {
    vi.clearAllMocks();
    aiStatus.mockResolvedValue([]);
  });

  it("switches 'KI antwortet zuerst' per topic and keeps the other topic untouched", async () => {
    renderTab(twoTopics());
    expect(screen.getByTestId("webchat-ai-topic-0-mode-off")).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByTestId("webchat-ai-topic-1-mode-first_line"));
    expect(screen.getByTestId("webchat-ai-topic-1-mode-first_line")).toHaveAttribute("aria-checked", "true");
    expect(lastDraft.values.topics.map((t) => t.ai_mode)).toEqual(["off", "first_line"]);
    expect(screen.getByRole("radiogroup", { name: "KI antwortet zuerst: Rechnung" })).toBeInTheDocument();
  });

  it("edits the assistant name and tone, name is required and capped at 80", () => {
    renderTab(twoTopics());
    const name = screen.getByTestId("webchat-bot-name");
    expect(name).toHaveValue("KI-Assistent");
    expect(name).toHaveAttribute("maxlength", "80");
    fireEvent.change(name, { target: { value: "Frieda" } });
    fireEvent.change(screen.getByTestId("webchat-bot-tone"), { target: { value: "Freundlich und kurz." } });
    expect(lastDraft.values.bot_name).toBe("Frieda");
    expect(lastDraft.values.bot_tone_prompt).toBe("Freundlich und kurz.");

    fireEvent.change(name, { target: { value: "  " } });
    expect(screen.getByTestId("webchat-bot-name-error")).toHaveTextContent("Bitte einen Namen für den Assistenten angeben.");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(validateDraft(lastDraft).bot_name).toBeDefined();
    fireEvent.change(name, { target: { value: "x".repeat(81) } });
    expect(validateDraft(lastDraft).bot_name).toContain("zu lang");
  });

  it("caps the tone at 2000 characters", () => {
    renderTab(twoTopics());
    expect(screen.getByTestId("webchat-bot-tone")).toHaveAttribute("maxlength", "2000");
    const d = twoTopics();
    d.values.bot_tone_prompt = "y".repeat(2001);
    expect(validateDraft(d).bot_tone_prompt).toContain("zu lang");
    d.values.bot_tone_prompt = "y".repeat(2000);
    expect(validateDraft(d).bot_tone_prompt).toBeUndefined();
  });

  it("lists the status per topic with a readable reason and links to the queue AI settings", async () => {
    aiStatus.mockResolvedValue([
      { topic_key: "support", label: "Support", queue_id: 1, queue_name: "Support", ai_mode: "first_line", ok: true, reason: null },
      { topic_key: "billing", label: "Rechnung", queue_id: 2, queue_name: "Buchhaltung", ai_mode: "first_line", ok: false, reason: "auto_reply_off" },
      { topic_key: "tech", label: "Technik", queue_id: 3, queue_name: null, ai_mode: "first_line", ok: false, reason: "worker_down" },
      { topic_key: "misc", label: "Sonst", queue_id: 4, queue_name: "Sonst", ai_mode: "first_line", ok: false, reason: "SomeError" },
    ]);
    renderTab(twoTopics());
    expect(await screen.findByTestId("webchat-ai-status-support-state")).toHaveTextContent("Bereit");
    expect(screen.getByTestId("webchat-ai-status-billing-state")).toHaveTextContent(
      "In den KI-Einstellungen der Queue sind automatische Antworten ausgeschaltet.",
    );
    expect(screen.getByTestId("webchat-ai-status-tech-state")).toHaveTextContent("Der KI-Dienst (tiqora-ai-worker) läuft gerade nicht.");
    expect(screen.getByTestId("webchat-ai-status-misc-state")).toHaveTextContent("derzeit nicht verfügbar (SomeError)");
    expect(aiStatus).toHaveBeenCalledWith(7, expect.anything());
    const link = screen.getByTestId("webchat-ai-queue-link");
    expect(link).toHaveTextContent("KI-Einstellungen der Queue öffnen");
    expect(link).toHaveAttribute("href", "/admin/ai/queues");
  });

  it("does not ask for a status before the first save", () => {
    renderTab(twoTopics(), null);
    expect(screen.getByTestId("webchat-ai-status-unsaved")).toBeInTheDocument();
    expect(aiStatus).not.toHaveBeenCalled();
  });

  it("shows a failed status load", async () => {
    aiStatus.mockRejectedValue(new Error("boom"));
    renderTab(twoTopics());
    await waitFor(() => expect(screen.getByTestId("webchat-ai-status-error")).toBeInTheDocument());
  });

  it("shows server field errors (R-ADMIN-422) on the topic and the name", () => {
    renderTab(twoTopics(), 7, {
      "topics.1.ai_mode": "Dieses Thema hat keine Queue.",
      bot_tone_prompt: "Ungültiger Tonfall.",
    });
    expect(screen.getByTestId("webchat-ai-topic-1-error")).toHaveTextContent("Dieses Thema hat keine Queue.");
    expect(screen.queryByTestId("webchat-ai-topic-0-error")).toBeNull();
    expect(screen.getByTestId("webchat-bot-tone-error")).toHaveTextContent("Ungültiger Tonfall.");
  });
});
