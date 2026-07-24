import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { WebhooksPage } from "./WebhooksPage";

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
    adminWebhooks: {
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
        <WebhooksPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleWebhook = {
  id: 9,
  name: "ticket-events",
  url: "https://example.com/hook",
  events: ["ticket.created", "ticket.closed"],
  valid: true,
  created: "2026-07-01T00:00:00Z",
  changed: "2026-07-01T00:00:00Z",
};

describe("WebhooksPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    list.mockResolvedValue({
      items: [sampleWebhook],
      total: 1,
      page: 1,
      page_size: 25,
    });
    create.mockResolvedValue({ ...sampleWebhook, id: 10, name: "article-events" });
  });

  it("renders the webhook list with resolved data", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("ticket-events")).toBeInTheDocument();
    });
    expect(screen.getByText("https://example.com/hook")).toBeInTheDocument();
    expect(screen.getByText("ticket.created, ticket.closed")).toBeInTheDocument();
  });

  it("opens the edit drawer via the row menu with fields populated", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("ticket-events")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-9"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-9"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-form-name")).toHaveValue("ticket-events");
    expect(screen.getByTestId("admin-form-url")).toHaveValue("https://example.com/hook");
    // Secret is never sent back by the API, so the edit form starts blank.
    expect(screen.getByTestId("admin-form-secret")).toHaveValue("");
    expect(screen.getByTestId("admin-form-events")).toHaveValue("ticket.created, ticket.closed");
    expect(screen.getByTestId("admin-form-valid")).toBeChecked();
  });

  it("submits a create with the mapped body", async () => {
    renderPage();
    await waitFor(() => expect(list).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("admin-new-button"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-form")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-form-name"), {
      target: { value: "article-events" },
    });
    fireEvent.change(screen.getByTestId("admin-form-url"), {
      target: { value: "https://example.com/articles" },
    });
    fireEvent.change(screen.getByTestId("admin-form-secret"), {
      target: { value: "s3cr3t" },
    });
    fireEvent.change(screen.getByTestId("admin-form-events"), {
      target: { value: "article.created, article.updated" },
    });

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledTimes(1);
    });
    expect(create).toHaveBeenCalledWith({
      name: "article-events",
      url: "https://example.com/articles",
      secret: "s3cr3t",
      events: ["article.created", "article.updated"],
      valid: true,
    });
  });

  it("deactivates a webhook via the row menu", async () => {
    deactivate.mockResolvedValue(undefined);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("ticket-events")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-9"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-9"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith(9);
    });
  });
});
