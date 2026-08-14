import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { QueueCustomerLinksPage } from "./QueueCustomerLinksPage";

const listQueues = vi.fn();
const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const remove = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminQueues: {
      list: (...args: unknown[]) => listQueues(...args),
    },
    adminQueueCustomerLinks: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      remove: (...args: unknown[]) => remove(...args),
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
        <QueueCustomerLinksPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const existingLink = {
  id: 5,
  queue_id: 3,
  queue_name: "Support",
  url_template: "https://netadmin.example/?u={customer_user}",
  admin_url_template: null,
  label: "Diagnose",
  visibility: "all",
  create_time: "2026-08-14T10:00:00Z",
  change_time: "2026-08-14T10:00:00Z",
};

describe("QueueCustomerLinksPage", () => {
  beforeEach(() => {
    listQueues.mockReset();
    list.mockReset();
    create.mockReset();
    update.mockReset();
    remove.mockReset();

    listQueues.mockResolvedValue({
      items: [
        { id: 3, name: "Support", valid_id: 1 },
        { id: 4, name: "Sales", valid_id: 1 },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });
    list.mockResolvedValue([existingLink]);
    create.mockResolvedValue({ ...existingLink, id: 99, queue_id: 4, label: "New" });
    remove.mockResolvedValue(undefined);
  });

  it("lists configured links with their queue name and visibility", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Support")).toBeInTheDocument());
    expect(screen.getByText("Diagnose")).toBeInTheDocument();
  });

  it("creates a new link for an unconfigured queue", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Support")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-customer-links-new"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-customer-links-form")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-customer-links-form-url_template"), {
      target: { value: "https://netadmin.example/?u={customer_user}" },
    });
    fireEvent.click(screen.getByTestId("admin-customer-links-form-submit"));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        url_template: "https://netadmin.example/?u={customer_user}",
        visibility: "all",
      }),
    );
  });

  it("deletes a link", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Support")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-5"));
    fireEvent.click(await screen.findByTestId("admin-row-delete-5"));

    await waitFor(() => expect(remove).toHaveBeenCalledWith(5));
  });
});
