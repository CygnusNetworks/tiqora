import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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

describe("GdprPage", () => {
  beforeEach(() => {
    preview.mockReset();
    createJob.mockReset();
    listJobs.mockReset();
    getJob.mockReset();
    rollback.mockReset();
    purgeBackup.mockReset();
    searchReferenceCustomers.mockReset();
    searchParams = {};

    preview.mockResolvedValue(previewPayload);
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
});
