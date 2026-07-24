import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { KbAttachments } from "./KbAttachments";

const { listKbAttachments, uploadKbAttachment, deleteKbAttachment, kbAttachmentDownloadUrl } =
  vi.hoisted(() => ({
    listKbAttachments: vi.fn(),
    uploadKbAttachment: vi.fn(),
    deleteKbAttachment: vi.fn(),
    kbAttachmentDownloadUrl: vi.fn(() => "/download"),
  }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      listKbAttachments,
      uploadKbAttachment,
      deleteKbAttachment,
      kbAttachmentDownloadUrl,
    },
  };
});

function wrap(articleId = 42) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <KbAttachments articleId={articleId} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("KbAttachments", () => {
  beforeEach(() => {
    listKbAttachments.mockReset();
    uploadKbAttachment.mockReset();
    deleteKbAttachment.mockReset();
    kbAttachmentDownloadUrl.mockReset().mockReturnValue("/download");
  });

  it("shows the empty state when there are no attachments", async () => {
    listKbAttachments.mockResolvedValue([]);
    wrap();
    await screen.findByTestId("kb-attachments-empty");
  });

  it("lists attachments with filename, size and a download link", async () => {
    listKbAttachments.mockResolvedValue([
      { id: 1, article_id: 42, filename: "manual.pdf", size: 2048 },
    ]);
    wrap();

    await screen.findByTestId("kb-attachment-1");
    const link = screen.getByText("manual.pdf");
    expect(link.closest("a")).toHaveAttribute("href", "/download");
    expect(kbAttachmentDownloadUrl).toHaveBeenCalledWith(42, 1);
    expect(screen.getByTestId("kb-attachment-1")).toHaveTextContent("2");
  });

  it("uploads the picked file and refreshes the list", async () => {
    listKbAttachments.mockResolvedValueOnce([]).mockResolvedValueOnce([
      { id: 9, article_id: 42, filename: "notes.txt", size: 10 },
    ]);
    uploadKbAttachment.mockResolvedValue({ id: 9, article_id: 42, filename: "notes.txt", size: 10 });
    wrap();

    await screen.findByTestId("kb-attachments-empty");

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    const input = screen.getByTestId("kb-attachment-input") as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [file] });
    fireEvent.change(input);

    await waitFor(() => expect(uploadKbAttachment).toHaveBeenCalledWith(42, file));
    await screen.findByTestId("kb-attachment-9");
    expect(input.value).toBe("");
  });

  it("shows a too-large error message on a 413 upload failure", async () => {
    listKbAttachments.mockResolvedValue([]);
    uploadKbAttachment.mockRejectedValue(new ApiError(413, "too large", "/kb/attachments"));
    wrap();

    await screen.findByTestId("kb-attachments-empty");
    const file = new File(["hello"], "big.bin");
    const input = screen.getByTestId("kb-attachment-input") as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [file] });

    // The upload handler's rejected promise isn't awaited by the caller, so
    // it surfaces as a Node unhandledRejection under Vitest — expected here
    // since we're deliberately testing the rejection path; swallow it.
    const onUnhandledRejection = () => {};
    process.on("unhandledRejection", onUnhandledRejection);
    try {
      fireEvent.change(input);

      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent("This file is too large (max 25 MB)."),
      );
    } finally {
      process.off("unhandledRejection", onUnhandledRejection);
    }
  });

  it("shows a generic upload-failed message on a non-413 upload error", async () => {
    listKbAttachments.mockResolvedValue([]);
    uploadKbAttachment.mockRejectedValue(new ApiError(500, "boom", "/kb/attachments"));
    wrap();

    await screen.findByTestId("kb-attachments-empty");
    const file = new File(["hello"], "big.bin");
    const input = screen.getByTestId("kb-attachment-input") as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [file] });

    const onUnhandledRejection = () => {};
    process.on("unhandledRejection", onUnhandledRejection);
    try {
      fireEvent.change(input);

      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent(
          "Could not upload this file. Please try again.",
        ),
      );
    } finally {
      process.off("unhandledRejection", onUnhandledRejection);
    }
  });

  it("deletes an attachment and removes it from the list", async () => {
    listKbAttachments
      .mockResolvedValueOnce([{ id: 1, article_id: 42, filename: "manual.pdf", size: 2048 }])
      .mockResolvedValueOnce([]);
    deleteKbAttachment.mockResolvedValue(undefined);
    wrap();

    await screen.findByTestId("kb-attachment-1");
    fireEvent.click(screen.getByTestId("kb-attachment-delete-1"));

    await waitFor(() => expect(deleteKbAttachment).toHaveBeenCalledWith(42, 1));
    await screen.findByTestId("kb-attachments-empty");
  });
});
