import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { TemplateAttachmentsPage } from "./TemplateAttachmentsPage";

const listTemplates = vi.fn();
const listAttachments = vi.fn();
const listTemplateAttachments = vi.fn();
const replaceTemplateAttachments = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminTemplates: {
      list: (...args: unknown[]) => listTemplates(...args),
    },
    adminAttachments: {
      list: (...args: unknown[]) => listAttachments(...args),
    },
    listTemplateAttachments: (...args: unknown[]) => listTemplateAttachments(...args),
    replaceTemplateAttachments: (...args: unknown[]) => replaceTemplateAttachments(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TemplateAttachmentsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("TemplateAttachmentsPage", () => {
  beforeEach(() => {
    listTemplates.mockReset();
    listAttachments.mockReset();
    listTemplateAttachments.mockReset();
    replaceTemplateAttachments.mockReset();

    listTemplates.mockResolvedValue({
      items: [{ id: 10, name: "Answer default", template_type: "Answer", valid_id: 1 }],
      total: 1,
      page: 1,
      page_size: 500,
    });
    listAttachments.mockResolvedValue({
      items: [
        {
          id: 1,
          name: "Logo",
          filename: "logo.png",
          content_type: "image/png",
          content: "abc",
          comments: null,
          valid_id: 1,
          create_time: "2026-01-01T00:00:00Z",
          change_time: "2026-01-01T00:00:00Z",
        },
        {
          id: 2,
          name: "AGB",
          filename: "agb.pdf",
          content_type: "application/pdf",
          content: "def",
          comments: null,
          valid_id: 1,
          create_time: "2026-01-01T00:00:00Z",
          change_time: "2026-01-01T00:00:00Z",
        },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });
    listTemplateAttachments.mockResolvedValue([
      { id: 1, name: "Logo", filename: "logo.png", content_type: "image/png" },
    ]);
    replaceTemplateAttachments.mockResolvedValue(undefined);
  });

  it("renders, loads assigned attachments, and submits the PUT replace body", async () => {
    renderPage();

    await screen.findByRole("option", { name: "Answer default" });
    fireEvent.change(screen.getByTestId("admin-template-attachments-select"), {
      target: { value: "10" },
    });

    await waitFor(() => {
      expect(listTemplateAttachments).toHaveBeenCalledWith(10);
    });

    await waitFor(() => {
      expect(screen.getByTestId("admin-template-attachment-toggle-1")).toBeChecked();
    });
    expect(screen.getByTestId("admin-template-attachment-toggle-2")).not.toBeChecked();

    fireEvent.click(screen.getByTestId("admin-template-attachment-toggle-2"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-template-attachments-save")).not.toBeDisabled();
    });
    fireEvent.click(screen.getByTestId("admin-template-attachments-save"));

    await waitFor(() => {
      expect(replaceTemplateAttachments).toHaveBeenCalled();
    });
    const [templateId, body] = replaceTemplateAttachments.mock.calls[0] as [
      number,
      { attachment_ids: number[] },
    ];
    expect(templateId).toBe(10);
    expect(body.attachment_ids).toEqual(expect.arrayContaining([1, 2]));
    expect(body.attachment_ids).toHaveLength(2);
  });
});
