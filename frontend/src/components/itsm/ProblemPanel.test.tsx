import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { api } from "@/lib/api";
import type { Problem } from "@/lib/itsmApi";
import { makeItsm, makeTicket, PERMS } from "./testSupport";
import { ProblemPanel } from "./ProblemPanel";

const { ticketItsm, get, save, addIncidents, markKnownError, closeIncidents, jobGet } = vi.hoisted(() => ({
  ticketItsm: vi.fn(),
  get: vi.fn(),
  save: vi.fn(),
  addIncidents: vi.fn(),
  markKnownError: vi.fn(),
  closeIncidents: vi.fn(),
  jobGet: vi.fn(),
}));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return {
    ...actual,
    itsmApi: { ...actual.itsmApi, ticketItsm, problem: { get, save, addIncidents, markKnownError, closeIncidents } },
  };
});
vi.mock("@/lib/jobsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/jobsApi")>("@/lib/jobsApi");
  return { ...actual, jobsApi: { ...actual.jobsApi, get: jobGet } };
});

function incident(id: number, closed = false) {
  return {
    ticket_id: id, tn: `2026070100${id}`, title: `Incident ${id}`, state: closed ? "closed successful" : "open",
    state_type: closed ? "closed" : "open", type_name: "Incident", closed,
  };
}

function problem(over: Partial<Problem> = {}): Problem {
  return {
    ticket_id: 7, root_cause: "Defekter Port", workaround: "Umstecken", known_error: false, known_error_article_id: null,
    known_error_article_state: null, known_error_since: null, incidents: [incident(9), incident(10), incident(11, true)],
    change_time: null, change_by: null, ...over,
  };
}

function renderPanel(ticket = makeTicket()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ProblemPanel ticket={ticket} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ProblemPanel", () => {
  beforeEach(() => {
    for (const f of [ticketItsm, get, save, addIncidents, markKnownError, closeIncidents, jobGet]) f.mockReset();
    ticketItsm.mockResolvedValue(makeItsm({ role: "problem" }));
    get.mockResolvedValue(problem());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders nothing for other roles or while the ITSM is disabled", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "incident" }));
    renderPanel();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalled());
    expect(screen.queryByTestId("problem-panel")).toBeNull();
    cleanup();
    ticketItsm.mockResolvedValue(makeItsm({ enabled: false, role: "problem" }));
    renderPanel();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("problem-panel")).toBeNull();
    expect(get).not.toHaveBeenCalled();
  });

  it("shows cause, workaround and incidents; rw edits and saves", async () => {
    save.mockResolvedValue(problem({ workaround: "Neu" }));
    renderPanel();
    const wa = (await screen.findByTestId("problem-workaround")) as HTMLTextAreaElement;
    expect(wa.value).toBe("Umstecken");
    expect((screen.getByTestId("problem-root-cause") as HTMLTextAreaElement).value).toBe("Defekter Port");
    expect(screen.getByTestId("problem-incidents").children).toHaveLength(3);
    expect(screen.queryByTestId("problem-save")).toBeNull();
    fireEvent.change(wa, { target: { value: "Neu" } });
    fireEvent.click(screen.getByTestId("problem-save"));
    await waitFor(() => expect(save).toHaveBeenCalledWith(7, { root_cause: "Defekter Port", workaround: "Neu" }));
  });

  it("is read-only without rw: no editing, no actions", async () => {
    renderPanel(makeTicket({ permissions: { ...PERMS, rw: false } }));
    const wa = (await screen.findByTestId("problem-workaround")) as HTMLTextAreaElement;
    expect(wa.readOnly).toBe(true);
    expect(screen.queryByTestId("problem-link-incidents")).toBeNull();
    expect(screen.queryByTestId("problem-known-error")).toBeNull();
    expect(screen.queryByTestId("problem-close-incidents")).toBeNull();
  });

  it("disables the known-error button with a hint while no KB category is configured", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "problem", known_error_category_configured: false }));
    renderPanel();
    const btn = (await screen.findByTestId("problem-known-error")) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByTestId("problem-known-error-hint").textContent).toBe(i18n.t("itsm.problem.knownError.noCategory"));
    fireEvent.click(btn);
    expect(markKnownError).not.toHaveBeenCalled();
  });

  it("publishes the known error and links the article with its review state", async () => {
    markKnownError.mockResolvedValue(
      problem({ known_error: true, known_error_article_id: 42, known_error_article_state: "review" }),
    );
    get
      .mockResolvedValueOnce(problem())
      .mockResolvedValue(problem({ known_error: true, known_error_article_id: 42, known_error_article_state: "review" }));
    renderPanel();
    fireEvent.click(await screen.findByTestId("problem-known-error"));
    await waitFor(() => expect(markKnownError).toHaveBeenCalledWith(7));
    const article = await screen.findByTestId("problem-article");
    expect(article.querySelector("a")?.getAttribute("href")).toBe("/agent/kb/42");
    expect(screen.getByTestId("problem-article-state").textContent).toBe(i18n.t("itsm.problem.knownError.states.review"));
  });

  it("shows a plain message when the server reports the missing category", async () => {
    const { ApiError } = await import("@/lib/api");
    markKnownError.mockRejectedValue(new ApiError(409, { detail: { code: "known_error_category_missing" } }, "/x"));
    renderPanel();
    fireEvent.click(await screen.findByTestId("problem-known-error"));
    expect((await screen.findByTestId("problem-known-error-error")).textContent).toBe(
      i18n.t("itsm.problem.knownError.noCategory"),
    );
  });

  it("closes incidents: confirm with the open count, follow the job, show the result", async () => {
    closeIncidents.mockResolvedValue({ job_id: 77, status: "queued" });
    jobGet.mockResolvedValue({
      id: 77, status: "succeeded", progress_done: 2, progress_total: 2, progress_message: null,
      cancel_requested: false, error: null, files: [], result: { done: [9, 10], skipped: [] },
    });
    renderPanel();
    fireEvent.click(await screen.findByTestId("problem-close-incidents"));
    expect(screen.getByTestId("problem-close-confirm").textContent).toBe(i18n.t("itsm.problem.close.confirm", { count: 2 }));
    expect(closeIncidents).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("problem-close-submit"));
    await waitFor(() => expect(closeIncidents).toHaveBeenCalledWith(7));
    const result = await screen.findByTestId("problem-close-result");
    expect(result.textContent).toContain(i18n.t("itsm.problem.close.done", { count: 2 }));
  });

  it("links incidents found by search (already linked ones are not selectable)", async () => {
    vi.spyOn(api, "searchTickets").mockResolvedValue([
      { ticket_id: 9, tn: "202607010009", title: "Schon da" },
      { ticket_id: 20, tn: "202607010020", title: "Neu" },
      { ticket_id: 7, tn: "20260701000007", title: "Selbst" },
    ]);
    addIncidents.mockResolvedValue(problem());
    renderPanel();
    fireEvent.click(await screen.findByTestId("problem-link-incidents"));
    fireEvent.change(screen.getByTestId("problem-incident-search"), { target: { value: "Neu" } });
    const hit = (await screen.findByTestId("problem-incident-hit-20")) as HTMLInputElement;
    expect((screen.getByTestId("problem-incident-hit-9") as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByTestId("problem-incident-hit-7")).toBeNull();
    fireEvent.click(hit);
    fireEvent.click(screen.getByTestId("problem-incident-submit"));
    await waitFor(() => expect(addIncidents).toHaveBeenCalledWith(7, [20]));
  });
});
