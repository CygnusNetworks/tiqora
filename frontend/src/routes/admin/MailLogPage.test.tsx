import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { MailLogPage } from "./MailLogPage";

const listMailLog = vi.fn();
const getMailLog = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listMailLog: (...args: unknown[]) => listMailLog(...args),
    getMailLog: (...args: unknown[]) => getMailLog(...args),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    ...rest
  }: {
    children: React.ReactNode;
    to?: string;
    params?: Record<string, string>;
  }) => (
    <a href="#" {...rest}>
      {children}
    </a>
  ),
}));

const sampleRows = [
  {
    id: 101,
    created_at: "2026-07-20T10:00:00",
    direction: "out",
    status: "sent",
    from_addr: "support@example.com",
    to_addr: "alice@example.com",
    cc_addr: null,
    subject: "Re: Help",
    message_id: "<out-1@example.com>",
    ticket_id: 42,
    article_id: 7,
    queue: "Support",
    smtp_code: 250,
    detail: "250 OK",
    duration_ms: 120,
  },
  {
    id: 102,
    created_at: "2026-07-20T10:05:00",
    direction: "in",
    status: "filtered",
    from_addr: "spam@example.com",
    to_addr: "support@example.com",
    cc_addr: null,
    subject: "Buy now",
    message_id: "<in-1@example.com>",
    ticket_id: null,
    article_id: null,
    queue: null,
    smtp_code: null,
    detail: "X-OTRS-Ignore",
    duration_ms: 5,
  },
];

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <MailLogPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("MailLogPage", () => {
  beforeEach(() => {
    listMailLog.mockReset();
    getMailLog.mockReset();
    listMailLog.mockResolvedValue({
      items: sampleRows,
      total: 2,
      page: 1,
      page_size: 25,
    });
    getMailLog.mockImplementation(async (id: number) =>
      sampleRows.find((r) => r.id === id),
    );
  });

  it("renders rows with direction icons and status badges", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("mail-log-table")).toBeInTheDocument();
    });

    expect(screen.getByTestId("mail-log-row-101")).toBeInTheDocument();
    expect(screen.getByTestId("mail-log-row-102")).toBeInTheDocument();
    expect(screen.getByTestId("mail-log-dir-out")).toBeInTheDocument();
    expect(screen.getByTestId("mail-log-dir-in")).toBeInTheDocument();
    expect(screen.getByTestId("mail-log-status-sent")).toHaveTextContent("sent");
    expect(screen.getByTestId("mail-log-status-filtered")).toHaveTextContent("filtered");
    expect(screen.getByText("Re: Help")).toBeInTheDocument();
  });

  it("passes direction filter to listMailLog", async () => {
    renderPage();
    await waitFor(() => expect(listMailLog).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("mail-log-filter-direction"));
    fireEvent.click(await screen.findByTestId("mail-log-filter-direction-panel-option-out"));

    await waitFor(() => {
      const last = listMailLog.mock.calls.at(-1)?.[0] as { direction?: string };
      expect(last?.direction).toBe("out");
    });
  });

  it("opens detail drawer on row click showing full detail", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("mail-log-row-101")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("mail-log-row-101"));

    await waitFor(() => {
      expect(screen.getByTestId("mail-log-drawer")).toBeInTheDocument();
    });
    expect(screen.getByTestId("mail-log-detail-body")).toHaveTextContent("250 OK");
    expect(getMailLog).toHaveBeenCalledWith(101, expect.anything());
  });
});
