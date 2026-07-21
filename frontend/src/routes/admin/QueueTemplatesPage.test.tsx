import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { QueueTemplatesPage } from "./QueueTemplatesPage";

const listQueues = vi.fn();
const listTemplates = vi.fn();
const listQueueTemplates = vi.fn();
const listTemplateQueues = vi.fn();
const assignQueueTemplate = vi.fn();
const revokeQueueTemplate = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminQueues: {
      list: (...args: unknown[]) => listQueues(...args),
    },
    adminTemplates: {
      list: (...args: unknown[]) => listTemplates(...args),
    },
    listQueueTemplates: (...args: unknown[]) => listQueueTemplates(...args),
    listTemplateQueues: (...args: unknown[]) => listTemplateQueues(...args),
    assignQueueTemplate: (...args: unknown[]) => assignQueueTemplate(...args),
    revokeQueueTemplate: (...args: unknown[]) => revokeQueueTemplate(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <QueueTemplatesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("QueueTemplatesPage", () => {
  beforeEach(() => {
    listQueues.mockReset();
    listTemplates.mockReset();
    listQueueTemplates.mockReset();
    listTemplateQueues.mockReset();
    assignQueueTemplate.mockReset();
    revokeQueueTemplate.mockReset();

    listQueues.mockResolvedValue({
      items: [{ id: 3, name: "Support", valid_id: 1 }],
      total: 1,
      page: 1,
      page_size: 500,
    });
    listTemplates.mockResolvedValue({
      items: [
        {
          id: 20,
          name: "Welcome",
          template_type: "Create",
          text: null,
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
    listQueueTemplates.mockResolvedValue([
      {
        id: 20,
        name: "Welcome",
        template_type: "Create",
        text: null,
        comments: null,
        valid_id: 1,
        create_time: "2026-01-01T00:00:00Z",
        change_time: "2026-01-01T00:00:00Z",
      },
    ]);
    listTemplateQueues.mockResolvedValue([]);
    assignQueueTemplate.mockResolvedValue(undefined);
    revokeQueueTemplate.mockResolvedValue(undefined);
  });

  it("preselects assigned templates and submits assign/revoke on toggle", async () => {
    renderPage();

    await screen.findByTestId("admin-queue-templates-page-anchor-3");
    fireEvent.click(screen.getByTestId("admin-queue-templates-page-anchor-3"));

    await waitFor(() => {
      expect(listQueueTemplates).toHaveBeenCalledWith(3, expect.anything());
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("admin-queue-templates-page-counterpart-20"),
      ).toBeChecked();
    });

    // Uncheck → revoke
    fireEvent.click(screen.getByTestId("admin-queue-templates-page-counterpart-20"));
    await waitFor(() => {
      expect(revokeQueueTemplate).toHaveBeenCalledWith(3, 20);
    });
  });
});
