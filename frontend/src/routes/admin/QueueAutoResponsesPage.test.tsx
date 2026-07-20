import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { QueueAutoResponsesPage } from "./QueueAutoResponsesPage";

const listQueues = vi.fn();
const listAutoResponses = vi.fn();
const assignQueueAutoResponse = vi.fn();
const revokeQueueAutoResponse = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminQueues: {
      list: (...args: unknown[]) => listQueues(...args),
    },
    adminAutoResponses: {
      list: (...args: unknown[]) => listAutoResponses(...args),
    },
    assignQueueAutoResponse: (...args: unknown[]) => assignQueueAutoResponse(...args),
    revokeQueueAutoResponse: (...args: unknown[]) => revokeQueueAutoResponse(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <QueueAutoResponsesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("QueueAutoResponsesPage", () => {
  beforeEach(() => {
    listQueues.mockReset();
    listAutoResponses.mockReset();
    assignQueueAutoResponse.mockReset();
    revokeQueueAutoResponse.mockReset();

    listQueues.mockResolvedValue({
      items: [{ id: 7, name: "Sales", valid_id: 1 }],
      total: 1,
      page: 1,
      page_size: 500,
    });
    listAutoResponses.mockResolvedValue({
      items: [
        {
          id: 11,
          name: "auto reply",
          text0: "hi",
          text1: "body",
          type_id: 1,
          system_address_id: 1,
          content_type: "text/plain",
          comments: null,
          valid_id: 1,
          create_time: "2026-01-01T00:00:00Z",
          change_time: "2026-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 500,
    });
    assignQueueAutoResponse.mockResolvedValue(undefined);
    revokeQueueAutoResponse.mockResolvedValue(undefined);
  });

  it("renders and submits PUT assign when an auto-response is toggled on", async () => {
    renderPage();

    await screen.findByRole("option", { name: "Sales" });
    fireEvent.change(screen.getByTestId("admin-queue-auto-responses-select"), {
      target: { value: "7" },
    });

    await screen.findByTestId("admin-queue-auto-response-toggle-11");
    fireEvent.click(screen.getByTestId("admin-queue-auto-response-toggle-11"));

    await waitFor(() => {
      expect(assignQueueAutoResponse).toHaveBeenCalledWith(7, 11);
    });
  });
});
