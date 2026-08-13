import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { TelegramChannelPage } from "./TelegramChannelPage";
import type { ChannelConfigOut } from "@/lib/api";

const getChannel = vi.fn();
const updateChannel = vi.fn();
const listQueues = vi.fn();
const telegramWebhookRegister = vi.fn();
const telegramWebhookUnregister = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminChannels: {
      get: (...args: unknown[]) => getChannel(...args),
      update: (...args: unknown[]) => updateChannel(...args),
    },
    adminQueues: {
      list: (...args: unknown[]) => listQueues(...args),
    },
    telegramWebhookRegister: (...args: unknown[]) => telegramWebhookRegister(...args),
    telegramWebhookUnregister: (...args: unknown[]) => telegramWebhookUnregister(...args),
  },
}));

function pollingConfig(overrides: Partial<ChannelConfigOut["config"]> = {}): ChannelConfigOut {
  return {
    channel: "telegram",
    enabled: true,
    config: {
      bot_token: "********",
      queue_name: "Raw",
      default_customer_user: "telegram@example.com",
      mode: "polling",
      webhook_url: null,
      webhook_secret_token: null,
      consent_required: "1",
      consent_text: "Please confirm.",
      consent_confirmed_text: "Thanks!",
      ...overrides,
    },
  };
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TelegramChannelPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("TelegramChannelPage", () => {
  beforeEach(() => {
    getChannel.mockReset();
    updateChannel.mockReset();
    listQueues.mockReset();
    telegramWebhookRegister.mockReset();
    telegramWebhookUnregister.mockReset();
    listQueues.mockResolvedValue({ items: [{ id: 1, name: "Raw" }], total: 1, page: 1, page_size: 200 });
  });

  it("loads config and renders fields, hiding webhook fields in polling mode", async () => {
    getChannel.mockResolvedValue(pollingConfig());
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("telegram-bot-token")).toBeInTheDocument();
    });

    expect(screen.getByTestId("telegram-bot-token")).toHaveValue("********");
    expect(screen.getByTestId("telegram-default-customer-user")).toHaveValue(
      "telegram@example.com",
    );
    expect(screen.getByTestId("telegram-enabled")).toBeChecked();
    expect(screen.getByTestId("telegram-consent-required")).toBeChecked();
    expect(screen.queryByTestId("telegram-webhook-url")).not.toBeInTheDocument();
    expect(screen.queryByTestId("telegram-webhook-register")).not.toBeInTheDocument();
  });

  it("shows webhook fields and register button when mode is webhook", async () => {
    getChannel.mockResolvedValue(
      pollingConfig({ mode: "webhook", webhook_url: "https://example.com/hook" }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("telegram-webhook-url")).toBeInTheDocument();
    });
    expect(screen.getByTestId("telegram-webhook-url")).toHaveValue("https://example.com/hook");
    expect(screen.getByTestId("telegram-webhook-register")).toBeInTheDocument();
    expect(screen.getByTestId("telegram-webhook-unregister")).toBeInTheDocument();
  });

  it("saves the form and sends the masked token as-is (no client-side filtering)", async () => {
    getChannel.mockResolvedValue(pollingConfig());
    updateChannel.mockResolvedValue(pollingConfig());
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("telegram-bot-token")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("telegram-save"));

    await waitFor(() => expect(updateChannel).toHaveBeenCalled());
    const [name, body] = updateChannel.mock.calls[0] as [string, Record<string, unknown>];
    expect(name).toBe("telegram");
    expect(body).toHaveProperty("enabled", true);
    const config = body.config as Record<string, string>;
    expect(config.bot_token).toBe("********");
    expect(config.queue_name).toBe("Raw");
    expect(config.consent_required).toBe("1");
  });

  it("calls telegramWebhookRegister when the register button is clicked", async () => {
    getChannel.mockResolvedValue(pollingConfig({ mode: "webhook" }));
    telegramWebhookRegister.mockResolvedValue({ ok: true, url: "https://example.com/hook" });
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("telegram-webhook-register")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("telegram-webhook-register"));
    await waitFor(() => expect(telegramWebhookRegister).toHaveBeenCalled());
    expect(screen.getByTestId("telegram-webhook-result")).toHaveTextContent(
      "https://example.com/hook",
    );
  });
});
