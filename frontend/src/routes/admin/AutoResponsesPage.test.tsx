import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AutoResponsesPage } from "./AutoResponsesPage";

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
    adminAutoResponses: {
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
        <AutoResponsesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleAutoResponse = {
  id: 4,
  name: "Ticket received",
  text0: "Your ticket",
  text1: "We received your request.",
  type_id: 1,
  system_address_id: 1,
  content_type: null,
  comments: null,
  valid_id: 1,
  assigned_queue_count: 2,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("AutoResponsesPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    list.mockResolvedValue({
      items: [sampleAutoResponse],
      total: 1,
      page: 1,
      page_size: 25,
    });
  });

  it("shows the auto response with its usage badge", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Ticket received")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-auto-response-usage-4")).toBeInTheDocument();
  });

  it("edits an existing auto response and sends the updated payload", async () => {
    update.mockResolvedValue({ ...sampleAutoResponse, name: "Updated response" });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Ticket received")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-4"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-4"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toHaveValue("Ticket received");
    });

    fireEvent.change(screen.getByTestId("admin-form-name"), {
      target: { value: "Updated response" },
    });
    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(update).toHaveBeenCalledWith(
        4,
        expect.objectContaining({
          name: "Updated response",
          type_id: 1,
          system_address_id: 1,
        }),
      );
    });
  });

  it("creates a new auto response from the drawer", async () => {
    create.mockResolvedValue({ ...sampleAutoResponse, id: 9, name: "New response" });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Ticket received")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-new-button"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-form-name"), {
      target: { value: "New response" },
    });
    fireEvent.change(screen.getByTestId("admin-form-type_id"), {
      target: { value: "3" },
    });
    fireEvent.change(screen.getByTestId("admin-form-system_address_id"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "New response",
          type_id: 3,
          system_address_id: 2,
        }),
      );
    });
  });

  it("deactivates an auto response via the row menu", async () => {
    deactivate.mockResolvedValue(undefined);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Ticket received")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-4"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-4"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith(4);
    });
  });
});
