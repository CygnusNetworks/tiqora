import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AiAclPage } from "./AiAclPage";

const listAcl = vi.fn();
const createAcl = vi.fn();
const updateAcl = vi.fn();
const deleteAcl = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    path: string;
    constructor(status: number, detail: unknown, path: string) {
      super(typeof detail === "string" ? detail : `HTTP ${status}`);
      this.name = "ApiError";
      this.status = status;
      this.path = path;
    }
  },
  api: {
    adminGroups: {
      list: () =>
        Promise.resolve({
          items: [
            { id: 3, name: "users", comments: null, valid_id: 1 },
            { id: 7, name: "support", comments: null, valid_id: 1 },
          ],
          total: 2,
          page: 1,
          page_size: 500,
        }),
    },
    adminRoles: {
      list: () => Promise.resolve({ items: [], total: 0, page: 1, page_size: 500 }),
    },
    listReferenceAgents: () => Promise.resolve([]),
  },
}));

vi.mock("@/lib/aiApi", () => ({
  aiApi: {
    listAcl: (...args: unknown[]) => listAcl(...args),
    createAcl: (...args: unknown[]) => createAcl(...args),
    updateAcl: (...args: unknown[]) => updateAcl(...args),
    deleteAcl: (...args: unknown[]) => deleteAcl(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AiAclPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiAclPage", () => {
  beforeEach(() => {
    listAcl.mockReset().mockResolvedValue([]);
    createAcl.mockReset();
    updateAcl.mockReset();
    deleteAcl.mockReset();
  });

  it("shows existing entries with resolved subject names", async () => {
    listAcl.mockResolvedValue([
      {
        id: 1,
        subject_type: "group",
        subject_id: 3,
        feature: "auto_reply",
        allowed: true,
        limit_requests_day: 100,
        limit_tokens_day: null,
        limit_requests_month: null,
      },
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-acl-table")).toBeInTheDocument());
    // Subject shown by NAME (users), not raw id.
    await waitFor(() => expect(screen.getByText(/users/)).toBeInTheDocument());
  });

  it("creates a new ACL entry via the drawer with a name-based subject picker", async () => {
    createAcl.mockResolvedValue({
      id: 5,
      subject_type: "group",
      subject_id: 3,
      feature: "auto_reply",
      allowed: true,
      limit_requests_day: null,
      limit_tokens_day: null,
      limit_requests_month: null,
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-ai-acl-new")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-ai-acl-new"));
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-acl-form-subject_id")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("admin-ai-acl-form-subject_id"));
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-acl-form-subject_id-menu-option-3")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("admin-ai-acl-form-subject_id-menu-option-3"));
    fireEvent.click(screen.getByTestId("admin-ai-acl-form-submit"));

    await waitFor(() => {
      expect(createAcl).toHaveBeenCalledWith(
        expect.objectContaining({ subject_type: "group", subject_id: 3, feature: "auto_reply" }),
      );
    });
  });
});
