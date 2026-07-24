import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AttachmentsPage } from "./AttachmentsPage";

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
    adminAttachments: {
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
        <AttachmentsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleAttachment = {
  id: 5,
  name: "Terms of service",
  content_type: "application/pdf",
  content: "YmFzZTY0",
  filename: "tos.pdf",
  comments: null,
  valid_id: 1,
  assigned_template_count: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("AttachmentsPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    list.mockResolvedValue({
      items: [sampleAttachment],
      total: 1,
      page: 1,
      page_size: 25,
    });
  });

  it("shows the attachment with filename, content type and usage badge", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Terms of service")).toBeInTheDocument();
    });
    expect(screen.getByText("tos.pdf")).toBeInTheDocument();
    expect(screen.getByText("application/pdf")).toBeInTheDocument();
    expect(screen.getByTestId("admin-attachment-usage-5")).toBeInTheDocument();
  });

  it("edits an existing attachment, preserving its content without re-upload", async () => {
    update.mockResolvedValue({ ...sampleAttachment, name: "Updated terms" });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Terms of service")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-5"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-5"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toHaveValue("Terms of service");
    });
    // Content is prefilled from the existing row, so required validation passes.
    expect(screen.getByTestId("admin-form-content-ready")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("admin-form-name"), {
      target: { value: "Updated terms" },
    });
    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(update).toHaveBeenCalledWith(
        5,
        expect.objectContaining({
          name: "Updated terms",
          content: "YmFzZTY0",
          filename: "tos.pdf",
          content_type: "application/pdf",
        }),
      );
    });
  });

  it("creates a new attachment by uploading a file", async () => {
    create.mockResolvedValue({ ...sampleAttachment, id: 11, name: "New attachment" });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Terms of service")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-new-button"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-form-name"), {
      target: { value: "New attachment" },
    });

    const file = new File(["hello world"], "hello.txt", { type: "text/plain" });
    fireEvent.change(screen.getByTestId("admin-form-content-file"), {
      target: { files: [file] },
    });

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-content-ready")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "New attachment",
          filename: "hello.txt",
          content: "aGVsbG8gd29ybGQ=",
          // The form's content_type field defaults to octet-stream and isn't
          // overwritten by the picked file's MIME type unless edited by hand.
          content_type: "application/octet-stream",
        }),
      );
    });
  });

  it("deactivates an attachment via the row menu", async () => {
    deactivate.mockResolvedValue(undefined);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Terms of service")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-5"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-5"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith(5);
    });
  });
});
