import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { TemplatesPage } from "./TemplatesPage";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const deactivate = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminTemplates: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      deactivate: (...args: unknown[]) => deactivate(...args),
    },
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TemplatesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("TemplatesPage usage badges", () => {
  beforeEach(() => {
    list.mockReset();
    list.mockResolvedValue({
      items: [
        {
          id: 1,
          name: "Welcome",
          text: "hi",
          content_type: null,
          template_type: "Answer",
          comments: null,
          valid_id: 1,
          create_time: "2026-01-01T00:00:00Z",
          change_time: "2026-01-01T00:00:00Z",
          assigned_queue_count: 3,
        },
        {
          id: 2,
          name: "Unused",
          text: null,
          content_type: null,
          template_type: "Note",
          comments: null,
          valid_id: 1,
          create_time: "2026-01-01T00:00:00Z",
          change_time: "2026-01-01T00:00:00Z",
          assigned_queue_count: 0,
        },
      ],
      total: 2,
      page: 1,
      page_size: 25,
    });
  });

  it("renders usage-count badges from assigned_queue_count", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-template-usage-1")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-template-usage-1").textContent).toMatch(/3/);
    expect(screen.getByTestId("admin-template-usage-2")).toHaveTextContent("0");
  });
});
