import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { DryRunDialog } from "./DryRunDialog";
import { prettyBody } from "./prettyBody";
import { demoDryRunJob } from "@/demo/giMock";

const dryRun = vi.fn();
vi.mock("@/lib/webservicesApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/webservicesApi")>("@/lib/webservicesApi");
  return { ...actual, webservicesApi: { ...actual.webservicesApi, dryRun: (...a: unknown[]) => dryRun(...a) } };
});
const get = vi.fn();
vi.mock("@/lib/jobsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/jobsApi")>("@/lib/jobsApi");
  return { ...actual, jobsApi: { ...actual.jobsApi, get: (...a: unknown[]) => get(...a) } };
});

function renderDialog() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DryRunDialog wsId={1} invoker="SyncTicket" events={["TicketCreate"]} onClose={vi.fn()} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("DryRunDialog", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });
  beforeEach(() => {
    dryRun.mockReset().mockResolvedValue({ job_id: 9390, status: "queued" });
    get.mockReset();
  });

  it("needs a valid ticket id before it can start", () => {
    renderDialog();
    const submit = screen.getByTestId("dryrun-submit");
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId("dryrun-ticket"), { target: { value: "abc" } });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId("dryrun-ticket"), { target: { value: "105" } });
    expect(submit).toBeEnabled();
  });

  it("starts the job, follows it and renders the masked request", async () => {
    get
      .mockResolvedValueOnce({ ...demoDryRunJob, status: "running", result: null, finished_at: null })
      .mockResolvedValue(demoDryRunJob);
    renderDialog();
    fireEvent.change(screen.getByTestId("dryrun-ticket"), { target: { value: "105" } });
    fireEvent.change(screen.getByTestId("dryrun-article"), { target: { value: "7" } });
    fireEvent.click(screen.getByTestId("dryrun-submit"));

    await waitFor(() =>
      expect(dryRun).toHaveBeenCalledWith(1, "SyncTicket", { ticket_id: 105, article_id: 7, event: "TicketCreate" }),
    );
    expect(await screen.findByTestId("dryrun-job")).toBeInTheDocument();
    expect(await screen.findByTestId("dryrun-result", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByTestId("dryrun-method")).toHaveTextContent("POST");
    expect(screen.getByTestId("dryrun-url")).toHaveTextContent("https://crm.example.org/api/v2/tickets");
    const headers = screen.getByTestId("dryrun-headers");
    expect(headers).toHaveTextContent("Authorization");
    expect(headers).toHaveTextContent("***");
    expect(headers).toHaveTextContent("application/json");
    // JSON body is pretty-printed
    expect(screen.getByTestId("dryrun-body").textContent).toContain('"TicketID": 105');
  });

  it("shows the API error when the job cannot be started", async () => {
    const { ApiError } = await import("@/lib/api");
    dryRun.mockRejectedValue(new ApiError(429, { detail: "Zu viele Aufträge" }, "/x"));
    renderDialog();
    fireEvent.change(screen.getByTestId("dryrun-ticket"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("dryrun-submit"));
    expect(await screen.findByTestId("dryrun-error")).toHaveTextContent("Zu viele Aufträge");
  });

  it("prettyBody keeps non-JSON text as it is", () => {
    expect(prettyBody('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(prettyBody("<xml/>")).toBe("<xml/>");
  });
});
