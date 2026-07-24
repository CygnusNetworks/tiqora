import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SalutationsPage } from "./SalutationsPage";

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
    adminSalutations: {
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
        <SalutationsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleSalutation = {
  id: 5,
  name: "formal",
  text: "Dear Mr/Mrs",
  content_type: "text/plain",
  comments: null,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("SalutationsPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    list.mockResolvedValue({
      items: [sampleSalutation],
      total: 1,
      page: 1,
      page_size: 25,
    });
    create.mockResolvedValue({ ...sampleSalutation, id: 6, name: "casual" });
    update.mockResolvedValue(sampleSalutation);
    deactivate.mockResolvedValue(undefined);
  });

  it("renders the list with resolved data", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("formal")).toBeInTheDocument();
    });
  });

  it("opens the edit drawer via the row menu with fields populated", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("formal")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-5"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-5"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-form-name")).toHaveValue("formal");
    expect(screen.getByTestId("admin-form-text")).toHaveValue("Dear Mr/Mrs");
  });

  it("submits a create with the entered field values", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("formal")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-new-button"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-form-name"), { target: { value: "casual" } });
    fireEvent.change(screen.getByTestId("admin-form-text"), {
      target: { value: "Hi there" },
    });

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith({
        name: "casual",
        text: "Hi there",
        comments: null,
        valid_id: 1,
      });
    });
  });

  it("deactivates a row via the row menu", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("formal")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-5"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-5"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith(5);
    });
  });
});
