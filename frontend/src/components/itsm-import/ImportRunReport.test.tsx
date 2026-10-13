import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ImportRunReport } from "./ImportRunReport";
import type { ImportRunOut } from "@/lib/itsmImportApi";

const jobGet = vi.fn();
vi.mock("@/lib/jobsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/jobsApi")>()),
  jobsApi: {
    get: (...a: unknown[]) => jobGet(...a),
    fileUrl: (j: number, f: number) => `/api/v1/jobs/${j}/files/${f}`,
  },
}));

const c = (created: number, problems = 0) => ({ created, updated: 0, unchanged: 0, skipped: 0, problems });

function run(over: Partial<ImportRunOut> = {}): ImportRunOut {
  return {
    id: 5,
    kind: "import",
    status: "done",
    started_at: "2026-10-12T09:00:00Z",
    finished_at: null,
    started_by_name: null,
    job_id: null,
    profile: null,
    message: null,
    issue_count: 0,
    issues: [],
    steps: {
      cis_pass1: c(4),
      cis_pass2: c(4),
      links: c(2, 1),
      attachments: c(0),
      priority_matrix: c(0),
      service_meta: c(0),
      changes: c(0),
      deleted_in_znuny: c(0),
      incident_states: c(0),
    },
    ...over,
  };
}

function renderReport(r: ImportRunOut) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ImportRunReport run={r} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ImportRunReport", () => {
  beforeEach(async () => {
    jobGet.mockReset().mockRejectedValue(new Error("gone"));
    await i18n.changeLanguage("de");
  });

  it("labels every step in German", () => {
    renderReport(run());
    for (const label of [
      "CIs (Grunddaten)",
      "CI-Versionen",
      "Verknüpfungen",
      "Anhänge",
      "Prioritätsmatrix",
      "Service-Angaben",
      "Changes (nur gezählt)",
      "In Znuny gelöscht",
      "Vorfallstatus",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByTestId("itsm-import-step-links")).toHaveTextContent("2");
  });

  it("shows an issue with the API's explanation", () => {
    renderReport(
      run({
        issue_count: 1,
        issues: [
          {
            step: "links",
            code: "link_target_missing",
            explanation: "Das Ziel einer Verknüpfung existiert nicht mehr.",
            object_ref: "CI 4711",
            message: "Ticket 99 fehlt",
          },
        ],
      }),
    );
    expect(screen.getByText("Das Ziel einer Verknüpfung existiert nicht mehr.")).toBeInTheDocument();
    expect(screen.getByText("Ticket 99 fehlt")).toBeInTheDocument();
    expect(screen.queryByTestId("itsm-import-no-issues")).toBeNull();
  });

  it("says so when there are no issues", () => {
    renderReport(run());
    expect(screen.getByText("Keine Auffälligkeiten")).toBeInTheDocument();
  });

  it("caps the list at 200 issues and links the CSV while the job exists", async () => {
    jobGet.mockReset().mockResolvedValue({
      id: 7,
      files: [{ id: 3, name: "itsm-import-bericht.csv", content_type: "text/csv", size_bytes: 10 }],
    });
    const issues = Array.from({ length: 250 }, (_, i) => ({
      step: "links",
      code: "x",
      explanation: "Erklärung",
      object_ref: `CI ${i}`,
      message: "",
    }));
    renderReport(run({ job_id: 7, issue_count: 250, issues }));
    expect(screen.getAllByRole("listitem")).toHaveLength(200);
    const link = await screen.findByRole("link", { name: "Vollständiger Bericht (CSV)" });
    expect(link).toHaveAttribute("href", "/api/v1/jobs/7/files/3");
  });
});
