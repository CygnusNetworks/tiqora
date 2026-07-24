import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { QueueVariablesPage } from "./QueueVariablesPage";

const request = vi.fn();
const listQueues = vi.fn();
const listQueuePhysicalVariables = vi.fn();
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
    request: (...args: unknown[]) => request(...args),
    adminQueues: {
      list: (...args: unknown[]) => listQueues(...args),
    },
    listQueuePhysicalVariables: (...args: unknown[]) => listQueuePhysicalVariables(...args),
    adminQueueVariables: {
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
        <QueueVariablesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const globalVar = {
  id: 10,
  queue_id: null,
  name: "Domain",
  value: "example.com",
  created: "2026-07-20T10:00:00Z",
  changed: "2026-07-20T10:00:00Z",
};

const queueVar = {
  id: 11,
  queue_id: 3,
  name: "Phone",
  value: "+49 123",
  created: "2026-07-20T10:00:00Z",
  changed: "2026-07-20T10:00:00Z",
};

describe("QueueVariablesPage", () => {
  beforeEach(() => {
    request.mockReset();
    listQueues.mockReset();
    listQueuePhysicalVariables.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    listQueues.mockResolvedValue({
      items: [{ id: 3, name: "Support", valid_id: 1 }],
      total: 1,
      page: 1,
      page_size: 500,
    });
    listQueuePhysicalVariables.mockResolvedValue([
      { name: "domain", value: "support.example" },
      { name: "phonenumber", value: "+49 30 123" },
    ]);
    request.mockImplementation(async (...args: unknown[]) => {
      const method = args[0] as string;
      const path = args[1] as string;
      const init = args[2] as { query?: Record<string, unknown> } | undefined;
      if (method === "GET" && path === "/api/v1/admin/queue-variables") {
        const q = init?.query ?? {};
        if (q.global_only === true || q.global_only === "true") {
          return { items: [globalVar], total: 1, page: 1, page_size: 500 };
        }
        if (Number(q.queue_id) === 3) {
          return { items: [queueVar], total: 1, page: 1, page_size: 500 };
        }
        return { items: [], total: 0, page: 1, page_size: 500 };
      }
      return undefined;
    });
    create.mockResolvedValue({ ...globalVar, id: 99, name: "NewVar" });
    deactivate.mockResolvedValue(undefined);
  });

  it("lists global variables by default", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("admin-queue-variables-page")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText("Domain")).toBeInTheDocument();
    });
    expect(screen.getByText("example.com")).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith(
      "GET",
      "/api/v1/admin/queue-variables",
      expect.objectContaining({
        query: expect.objectContaining({ global_only: true }),
      }),
    );
  });

  it("lists a queue's variables when a queue is selected", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Domain")).toBeInTheDocument());
    await waitFor(() => expect(listQueues).toHaveBeenCalled());
    // Global scope: physical section hidden.
    expect(screen.queryByTestId("admin-queue-variables-physical")).not.toBeInTheDocument();

    // Open the SelectField menu; the Support option renders in its portal
    // panel once the queue list has loaded.
    fireEvent.click(screen.getByTestId("admin-queue-variables-queue-select"));
    await waitFor(() => {
      expect(
        screen.getByTestId("admin-queue-variables-queue-select-menu-option-3"),
      ).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("admin-queue-variables-queue-select-menu-option-3"));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "GET",
        "/api/v1/admin/queue-variables",
        expect.objectContaining({
          query: expect.objectContaining({ queue_id: 3 }),
        }),
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Phone")).toBeInTheDocument();
    });
    expect(screen.getByText("+49 123")).toBeInTheDocument();

    // Physical columns section for a specific queue.
    await waitFor(() => {
      expect(listQueuePhysicalVariables).toHaveBeenCalledWith(3, expect.anything());
    });
    await waitFor(() => {
      expect(screen.getByTestId("admin-queue-variables-physical")).toBeInTheDocument();
    });
    expect(screen.getByText("<OTRS_QUEUE_domain>")).toBeInTheDocument();
    expect(screen.getByText("support.example")).toBeInTheDocument();
    expect(screen.getByText("<OTRS_QUEUE_phonenumber>")).toBeInTheDocument();
  });

  it("creates a new global variable", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Domain")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-queue-variables-new"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-queue-variables-form")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-queue-variables-form-name"), {
      target: { value: "NewVar" },
    });
    fireEvent.change(screen.getByTestId("admin-queue-variables-form-value"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByTestId("admin-queue-variables-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledTimes(1);
    });
    expect(create).toHaveBeenCalledWith({
      name: "NewVar",
      value: "hello",
      queue_id: null,
    });
  });

  it("deletes a variable via deactivate (hard DELETE)", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Domain")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-10"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-10"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith(10);
    });
  });
});
