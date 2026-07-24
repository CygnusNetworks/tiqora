import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SignaturesPage } from "./SignaturesPage";

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
    adminSignatures: {
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
        <SignaturesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleSignature = {
  id: 9,
  name: "default",
  text: "Kind regards,\nSupport Team",
  content_type: "text/plain",
  comments: null,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("SignaturesPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    list.mockResolvedValue({
      items: [sampleSignature],
      total: 1,
      page: 1,
      page_size: 25,
    });
    create.mockResolvedValue({ ...sampleSignature, id: 10, name: "short" });
    update.mockResolvedValue(sampleSignature);
    deactivate.mockResolvedValue(undefined);
  });

  it("renders the list with resolved data", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("default")).toBeInTheDocument();
    });
  });

  it("opens the edit drawer via the row menu with fields populated", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("default")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-9"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-9"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-form-name")).toHaveValue("default");
    expect(screen.getByTestId("admin-form-text")).toHaveValue("Kind regards,\nSupport Team");
  });

  it("submits a create with the entered field values", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("default")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-new-button"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-form-name"), { target: { value: "short" } });
    fireEvent.change(screen.getByTestId("admin-form-text"), {
      target: { value: "Best,\nTeam" },
    });

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith({
        name: "short",
        text: "Best,\nTeam",
        comments: null,
        valid_id: 1,
      });
    });
  });

  it("deactivates a row via the row menu", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("default")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-9"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-9"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith(9);
    });
  });
});
