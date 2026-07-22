import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { GdprPage } from "./GdprPage";

const preview = vi.fn();
const createJob = vi.fn();
const listJobs = vi.fn();
const getJob = vi.fn();
const rollback = vi.fn();
const purgeBackup = vi.fn();
const backupDownloadUrl = vi.fn((id: number) => `/api/v1/admin/gdpr/jobs/${id}/backup/download`);
const selectorCount = vi.fn();
const recordPreview = vi.fn();
const searchReferenceCustomers = vi.fn();

let searchParams: { logins?: string; tab?: string } = {};

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => searchParams,
  useNavigate: () => vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    adminGdpr: {
      preview: (...args: unknown[]) => preview(...args),
      createJob: (...args: unknown[]) => createJob(...args),
      listJobs: (...args: unknown[]) => listJobs(...args),
      getJob: (...args: unknown[]) => getJob(...args),
      rollback: (...args: unknown[]) => rollback(...args),
      purgeBackup: (...args: unknown[]) => purgeBackup(...args),
      backupDownloadUrl: (id: number) => backupDownloadUrl(id),
      selectorCount: (...args: unknown[]) => selectorCount(...args),
      recordPreview: (...args: unknown[]) => recordPreview(...args),
    },
    searchReferenceCustomers: (...args: unknown[]) => searchReferenceCustomers(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <GdprPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const previewPayload = {
  mode: "anonymize",
  customers: [
    {
      id: 10,
      login: "alice@example.com",
      email: "alice@example.com",
      customer_id: "ACME",
    },
    {
      id: 11,
      login: "bob@example.com",
      email: "bob@example.com",
      customer_id: "ACME",
    },
  ],
  counts: {
    customer_user: 2,
    customer_company: 1,
    tickets: 5,
    articles: 12,
    article_data_mime_attachment: 3,
    article_search_index: 12,
  },
  sample: [],
  columns_changed: {
    customer_user: ["email", "first_name", "last_name"],
  },
  tables_deleted: [],
};

const recordPreviewAnonymizePayload = {
  login: "alice@example.com",
  mode: "anonymize",
  fields: [
    {
      field: "customer_user.login",
      before: "alice@example.com",
      after: "gdpr-user-10",
      changed: true,
      occurrences: null,
    },
    {
      field: "customer_user.phone",
      before: null,
      after: null,
      changed: false,
      occurrences: null,
    },
  ],
  delete_summary: [],
};

const recordPreviewDeletePayload = {
  login: "alice@example.com",
  mode: "delete",
  fields: [],
  delete_summary: [
    { table: "customer_user", count: 1 },
    { table: "tickets", count: 3 },
  ],
};

describe("GdprPage", () => {
  beforeEach(() => {
    preview.mockReset();
    createJob.mockReset();
    listJobs.mockReset();
    getJob.mockReset();
    rollback.mockReset();
    purgeBackup.mockReset();
    selectorCount.mockReset();
    recordPreview.mockReset();
    searchReferenceCustomers.mockReset();
    searchParams = {};

    preview.mockResolvedValue(previewPayload);
    selectorCount.mockResolvedValue({ count: 2 });
    recordPreview.mockResolvedValue(recordPreviewAnonymizePayload);
    createJob.mockResolvedValue({
      id: 99,
      mode: "anonymize",
      selector: "{}",
      resolved_logins: '["alice@example.com","bob@example.com"]',
      status: "applied",
      counts: "{}",
      seed: null,
      actor: "admin",
      force_parallel: false,
      created: "2026-07-20T10:00:00Z",
      applied_at: "2026-07-20T10:00:01Z",
      rolled_back_at: null,
      backup_expires_at: "2026-08-20T10:00:00Z",
      counts_parsed: { customer_user: 2 },
      resolved_logins_parsed: ["alice@example.com", "bob@example.com"],
      selector_parsed: { logins: ["alice@example.com"] },
    });
    listJobs.mockResolvedValue({
      items: [
        {
          id: 7,
          mode: "delete",
          selector: '{"logins":["x"]}',
          resolved_logins: '["x"]',
          status: "applied",
          counts: "{}",
          seed: null,
          actor: "admin",
          force_parallel: false,
          created: "2026-07-19T10:00:00Z",
          applied_at: "2026-07-19T10:00:01Z",
          rolled_back_at: null,
          backup_expires_at: "2026-08-19T10:00:00Z",
        },
        {
          id: 8,
          mode: "anonymize",
          selector: "{}",
          resolved_logins: "[]",
          status: "purged",
          counts: "{}",
          seed: null,
          actor: "admin",
          force_parallel: false,
          created: "2026-07-18T10:00:00Z",
          applied_at: "2026-07-18T10:00:01Z",
          rolled_back_at: null,
          backup_expires_at: "2026-08-18T10:00:00Z",
        },
      ],
      total: 2,
      page: 1,
      page_size: 25,
    });
    rollback.mockResolvedValue({ restored_rows: 4 });
    purgeBackup.mockResolvedValue({ deleted_backups: 1 });
    searchReferenceCustomers.mockResolvedValue([]);
  });

  it("previews resolved customers and affected counts", async () => {
    renderPage();

    fireEvent.change(screen.getByTestId("gdpr-login-input"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByTestId("gdpr-login-add"));
    fireEvent.click(screen.getByTestId("gdpr-preview"));

    await waitFor(() => {
      expect(preview).toHaveBeenCalled();
    });
    expect(preview).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: "anonymize",
        selector: expect.objectContaining({
          logins: ["alice@example.com"],
        }),
      }),
    );

    await waitFor(() => {
      expect(screen.getByTestId("gdpr-preview-panel")).toBeInTheDocument();
    });
    const panel = screen.getByTestId("gdpr-preview-panel");
    expect(panel).toHaveTextContent("alice@example.com");
    expect(panel).toHaveTextContent("bob@example.com");
    expect(screen.getByTestId("gdpr-count-tickets")).toHaveTextContent("5");
    expect(screen.getByTestId("gdpr-count-customer_user")).toHaveTextContent("2");
    expect(screen.getByTestId("gdpr-count-article_search_index")).toHaveTextContent("12");
  });

  it("requires typing LÖSCHEN before delete run", async () => {
    renderPage();

    fireEvent.click(screen.getByTestId("gdpr-mode-delete"));
    fireEvent.change(screen.getByTestId("gdpr-login-input"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByTestId("gdpr-login-add"));
    fireEvent.click(screen.getByTestId("gdpr-preview"));

    await waitFor(() => {
      expect(screen.getByTestId("gdpr-run")).toBeEnabled();
    });
    fireEvent.click(screen.getByTestId("gdpr-run"));

    await waitFor(() => {
      expect(screen.getByTestId("gdpr-confirm-dialog")).toBeInTheDocument();
    });

    const submit = screen.getByTestId("gdpr-confirm-submit");
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByTestId("gdpr-confirm-type"), {
      target: { value: "wrong" },
    });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByTestId("gdpr-confirm-type"), {
      target: { value: "LÖSCHEN" },
    });
    expect(submit).toBeEnabled();

    fireEvent.click(submit);
    await waitFor(() => {
      expect(createJob).toHaveBeenCalledWith(
        expect.objectContaining({
          mode: "delete",
          confirm: true,
          customer_user_ids: [10, 11],
        }),
      );
    });
  });

  it("jobs list exposes rollback, purge and download actions", async () => {
    renderPage();
    fireEvent.click(screen.getByTestId("gdpr-tab-jobs"));

    await waitFor(() => {
      expect(listJobs).toHaveBeenCalled();
      expect(screen.getByTestId("gdpr-job-rollback-7")).toBeInTheDocument();
    });

    // applied job: rollback + purge enabled; download link present
    expect(screen.getByTestId("gdpr-job-rollback-7")).not.toBeDisabled();
    expect(screen.getByTestId("gdpr-job-purge-7")).not.toBeDisabled();
    expect(screen.getByTestId("gdpr-job-download-7")).toHaveAttribute(
      "href",
      "/api/v1/admin/gdpr/jobs/7/backup/download",
    );

    // purged job: rollback/purge disabled
    expect(screen.getByTestId("gdpr-job-rollback-8")).toBeDisabled();
    expect(screen.getByTestId("gdpr-job-purge-8")).toBeDisabled();

    fireEvent.click(screen.getByTestId("gdpr-job-rollback-7"));
    await waitFor(() => {
      expect(screen.getByTestId("gdpr-job-action-dialog")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("gdpr-job-action-confirm"));
    await waitFor(() => {
      expect(rollback).toHaveBeenCalledWith(7);
    });
  });

  it("prefills logins from search params", async () => {
    searchParams = { logins: "prefill@example.com,other@example.com" };
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("gdpr-login-list")).toBeInTheDocument();
    });
    expect(screen.getByText("prefill@example.com")).toBeInTheDocument();
    expect(screen.getByText("other@example.com")).toBeInTheDocument();
  });

  it("anonymize confirm does not require typed word", async () => {
    renderPage();
    fireEvent.change(screen.getByTestId("gdpr-login-input"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByTestId("gdpr-login-add"));
    fireEvent.click(screen.getByTestId("gdpr-preview"));
    await waitFor(() => expect(screen.getByTestId("gdpr-run")).toBeEnabled());
    fireEvent.click(screen.getByTestId("gdpr-run"));

    await waitFor(() => {
      expect(screen.getByTestId("gdpr-confirm-dialog")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("gdpr-confirm-type")).not.toBeInTheDocument();
    expect(screen.getByTestId("gdpr-confirm-submit")).toBeEnabled();
  });

  it("adds and removes a chip-baukasten filter (login regex)", async () => {
    renderPage();

    fireEvent.click(screen.getByTestId("gdpr-add-filter"));
    await waitFor(() => {
      expect(screen.getByTestId("gdpr-add-filter-panel")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("gdpr-add-filter-panel-option-loginRegex"));

    await waitFor(() => {
      expect(screen.getByTestId("gdpr-filter-open-loginRegex")).toBeInTheDocument();
    });
    const input = within(screen.getByTestId("gdpr-filter-open-loginRegex")).getByRole("textbox");
    fireEvent.change(input, { target: { value: "^old-.*" } });
    fireEvent.click(screen.getByTestId("gdpr-filter-commit-loginRegex"));

    await waitFor(() => {
      expect(screen.getByTestId("gdpr-filter-chip-loginRegex")).toHaveTextContent("^old-.*");
    });
    expect(screen.queryByTestId("gdpr-filter-open-loginRegex")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("gdpr-preview"));
    await waitFor(() => {
      expect(preview).toHaveBeenCalledWith(
        expect.objectContaining({
          selector: expect.objectContaining({ login_regex: "^old-.*" }),
        }),
      );
    });

    fireEvent.click(screen.getByTestId("gdpr-filter-chip-remove-loginRegex"));
    await waitFor(() => {
      expect(screen.queryByTestId("gdpr-filter-chip-loginRegex")).not.toBeInTheDocument();
    });
  });

  it("shows a debounced live match count for the active selector", async () => {
    renderPage();

    fireEvent.change(screen.getByTestId("gdpr-login-input"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByTestId("gdpr-login-add"));

    await waitFor(
      () => {
        expect(selectorCount).toHaveBeenCalledWith(
          expect.objectContaining({
            selector: expect.objectContaining({ logins: ["alice@example.com"] }),
          }),
          expect.anything(),
        );
      },
      { timeout: 2000 },
    );
    await waitFor(() => {
      expect(screen.getByTestId("gdpr-live-count")).toHaveTextContent("2");
    });
  });

  it("flags zero live matches", async () => {
    selectorCount.mockResolvedValue({ count: 0 });
    renderPage();

    fireEvent.change(screen.getByTestId("gdpr-login-input"), {
      target: { value: "nobody@example.com" },
    });
    fireEvent.click(screen.getByTestId("gdpr-login-add"));

    await waitFor(
      () => {
        expect(screen.getByTestId("gdpr-live-count")).toHaveTextContent("0");
      },
      { timeout: 2000 },
    );
    expect(screen.getByTestId("gdpr-live-count-hint")).toBeInTheDocument();
  });

  it("expands the record preview accordion and shows before/after with unchanged state", async () => {
    renderPage();

    fireEvent.change(screen.getByTestId("gdpr-login-input"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByTestId("gdpr-login-add"));
    fireEvent.click(screen.getByTestId("gdpr-preview"));

    await waitFor(() => {
      expect(screen.getByTestId("gdpr-record-preview-toggle-alice@example.com")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("gdpr-record-preview-toggle-alice@example.com"));

    await waitFor(() => {
      expect(
        screen.getByTestId("gdpr-record-preview-panel-alice@example.com"),
      ).toBeInTheDocument();
    });
    expect(recordPreview).toHaveBeenCalledWith(
      expect.objectContaining({ login: "alice@example.com", mode: "anonymize" }),
      expect.anything(),
    );
    const panel = screen.getByTestId("gdpr-record-preview-panel-alice@example.com");
    expect(panel).toHaveTextContent("gdpr-user-10");
    expect(panel).toHaveTextContent("unchanged");
  });

  it("shows delete_summary in the accordion for delete mode", async () => {
    recordPreview.mockResolvedValue(recordPreviewDeletePayload);
    renderPage();

    fireEvent.click(screen.getByTestId("gdpr-mode-delete"));
    fireEvent.change(screen.getByTestId("gdpr-login-input"), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByTestId("gdpr-login-add"));
    fireEvent.click(screen.getByTestId("gdpr-preview"));

    await waitFor(() => {
      expect(screen.getByTestId("gdpr-record-preview-toggle-alice@example.com")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("gdpr-record-preview-toggle-alice@example.com"));

    await waitFor(() => {
      expect(
        screen.getByTestId("gdpr-record-preview-panel-alice@example.com"),
      ).toBeInTheDocument();
    });
    const panel = screen.getByTestId("gdpr-record-preview-panel-alice@example.com");
    expect(panel).toHaveTextContent("customer_user");
    expect(panel).toHaveTextContent("tickets");
  });
});
