import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { LinkedNoteDialog } from "./LinkedNoteDialog";

const { linkedArticles, jobGet } = vi.hoisted(() => ({ linkedArticles: vi.fn(), jobGet: vi.fn() }));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, linkedArticles } };
});
vi.mock("@/lib/jobsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/jobsApi")>("@/lib/jobsApi");
  return { ...actual, jobsApi: { ...actual.jobsApi, get: jobGet } };
});

const TARGETS = [
  { id: 9, tn: "2026070100009", title: "Neun" },
  { id: 10, tn: "2026070100010", title: "Zehn" },
];

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <LinkedNoteDialog ticketId={7} targets={TARGETS} onClose={() => {}} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("LinkedNoteDialog", () => {
  beforeEach(() => {
    linkedArticles.mockReset();
    jobGet.mockReset();
  });

  it("preselects all targets, submits the selection, shows progress and the result", async () => {
    linkedArticles.mockResolvedValue({ job_id: 55, status: "queued" });
    jobGet.mockResolvedValue({
      id: 55, status: "succeeded", progress_done: 2, progress_total: 2, progress_message: null,
      cancel_requested: false, error: null, files: [],
      result: { done: [9], skipped: [{ ticket_id: 10, reason: "no_rw", detail: null }] },
    });
    wrap();
    expect((screen.getByTestId("linked-note-target-9") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("linked-note-target-10") as HTMLInputElement).checked).toBe(true);
    const submit = screen.getByTestId("linked-note-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(screen.getByTestId("linked-note-subject"), { target: { value: "Update" } });
    const editor = screen.getByTestId("linked-note-body");
    editor.innerHTML = "Text";
    fireEvent.input(editor);
    fireEvent.click(screen.getByTestId("linked-note-visible"));
    fireEvent.click(screen.getByTestId("linked-note-target-10"));
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);

    await waitFor(() =>
      expect(linkedArticles).toHaveBeenCalledWith(7, {
        target_ticket_ids: [9],
        mode: "note",
        subject: "Update",
        body_html: "Text",
        visible_for_customer: true,
      }),
    );
    expect(await screen.findByTestId("linked-note-job")).toBeTruthy();
    expect((await screen.findByTestId("linked-note-done")).textContent).toContain("2026070100009");
    const skipped = screen.getByTestId("linked-note-skipped");
    expect(skipped.textContent).toContain("2026070100010");
    expect(skipped.textContent).toMatch(/Schreibberechtigung|write access/);
  });

  it("names a plugin veto with its reason", async () => {
    linkedArticles.mockResolvedValue({ job_id: 56, status: "queued" });
    jobGet.mockResolvedValue({
      id: 56, status: "succeeded", progress_done: 2, progress_total: 2, progress_message: null,
      cancel_requested: false, error: null, files: [],
      result: {
        done: [9],
        skipped: [{ ticket_id: 10, reason: "vetoed", detail: "Vertragsnummer fehlt" }],
      },
    });
    wrap();
    fireEvent.change(screen.getByTestId("linked-note-subject"), { target: { value: "Update" } });
    const editor = screen.getByTestId("linked-note-body");
    editor.innerHTML = "Text";
    fireEvent.input(editor);
    const submit = screen.getByTestId("linked-note-submit") as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);
    const skipped = await screen.findByTestId("linked-note-skipped");
    expect(skipped.textContent).toContain("2026070100010");
    expect(skipped.textContent).toMatch(/von einer Erweiterung abgelehnt|refused by an extension/);
    expect(skipped.textContent).toContain("(Vertragsnummer fehlt)");
  });
});
