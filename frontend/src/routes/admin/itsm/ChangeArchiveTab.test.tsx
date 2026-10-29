import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithClient } from "@/components/itsm/change/changeTestSupport";
import { DEMO_ARCHIVE_DRY_JOB_ID, DEMO_ARCHIVE_JOB_ID, resolveChangeData } from "@/demo/changeMock";
import type { LegacyArchiveStatus } from "@/lib/changeAdminApi";
import type { Job } from "@/lib/jobsApi";
import { bodyOf, serveChangeAdmin, useGerman, type Recorded } from "./changeAdminTestUtils";
import { ChangeArchiveTab } from "./ChangeArchiveTab";

beforeAll(useGerman);
afterEach(() => vi.restoreAllMocks());

const BASE = "/api/v1/admin/itsm/change/legacy-archive";
const status = (over: Partial<LegacyArchiveStatus> = {}): LegacyArchiveStatus => ({
  archived: 2,
  last_archived_at: "2026-10-12T08:00:00Z",
  can_start: true,
  blocked_code: null,
  blocked_message: null,
  active_job_id: null,
  ...over,
});

describe("ChangeArchiveTab", () => {
  it("shows how many changes are archived and since when; search covers number and title", async () => {
    serveChangeAdmin();
    renderWithClient(<ChangeArchiveTab />);
    expect(await screen.findByTestId("archive-count")).toHaveTextContent("Archivierte Changes: 2");
    expect(screen.getByTestId("archive-last")).toHaveTextContent("Zuletzt archiviert: 2026-10-12 08:00 (UTC)");
    expect(screen.getByTestId("change-archive-tab")).toHaveTextContent("Die Suche in den archivierten Changes findet Nummer und Titel.");
  });

  it("runs a dry run as a job and shows its counts", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<ChangeArchiveTab />);
    fireEvent.click(await screen.findByTestId("archive-dry-run"));
    const result = await screen.findByTestId("archive-result");
    expect(bodyOf(requests, "POST", BASE)).toEqual({ dry_run: true });
    expect(screen.getByTestId("archive-job-status")).toHaveTextContent("Fertig");
    expect(result).toHaveTextContent("Ergebnis des Probelaufs (nichts geschrieben)");
    expect(within(result).getByTestId("archive-result-created")).toHaveTextContent("1");
    expect(within(result).getByTestId("archive-result-unchanged")).toHaveTextContent("1");
    expect(within(result).getByTestId("archive-result-skipped")).toHaveTextContent("0");
    expect(within(result).getByTestId("archive-not-archived")).toHaveTextContent("Change-Vorlagen: 2");
    expect(within(result).getByTestId("archive-not-archived")).toHaveTextContent("Bedingungen: 1");
  });

  it("runs the real archive", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<ChangeArchiveTab />);
    fireEvent.click(await screen.findByTestId("archive-run"));
    const result = await screen.findByTestId("archive-result");
    expect(bodyOf(requests, "POST", BASE)).toEqual({ dry_run: false });
    expect(result).toHaveTextContent("Ergebnis");
    expect(result).not.toHaveTextContent("Probelauf");
  });

  it("follows a run that is already active and offers no second one", async () => {
    const running = { ...(resolveChangeData(`/api/v1/jobs/${DEMO_ARCHIVE_JOB_ID}`, "GET", "", "tiqora") as Job), status: "running", finished_at: null, result: null };
    serveChangeAdmin([], (method, path) => {
      if (method === "GET" && path === BASE) return status({ active_job_id: DEMO_ARCHIVE_JOB_ID });
      if (method === "GET" && path === `/api/v1/jobs/${DEMO_ARCHIVE_JOB_ID}`) return running;
      return undefined;
    });
    renderWithClient(<ChangeArchiveTab />);
    expect(await screen.findByTestId("archive-active")).toHaveTextContent("Ein Lauf ist bereits aktiv.");
    expect(await screen.findByTestId("archive-job-status")).toHaveTextContent("Läuft");
    expect(screen.getByTestId("archive-dry-run")).toBeDisabled();
    expect(screen.getByTestId("archive-run")).toBeDisabled();
  });

  it("says so when the start was answered with a dry run that was already running", async () => {
    serveChangeAdmin([], (method, path) =>
      method === "POST" && path === BASE ? { job_id: DEMO_ARCHIVE_DRY_JOB_ID, status: "running" } : undefined,
    );
    renderWithClient(<ChangeArchiveTab />);
    fireEvent.click(await screen.findByTestId("archive-dry-run"));
    expect(await screen.findByTestId("archive-joined")).toHaveTextContent("Es lief bereits ein Lauf dieser Art.");
    await waitFor(() => expect(screen.getByTestId("archive-result")).toHaveTextContent("Probelauf"));
  });

  it("recognises a still queued run that another admin started after the page loaded", async () => {
    let statusCalls = 0;
    serveChangeAdmin([], (method, path) => {
      if (method === "GET" && path === BASE) {
        statusCalls += 1;
        return status({ active_job_id: statusCalls === 1 ? null : DEMO_ARCHIVE_DRY_JOB_ID });
      }
      if (method === "POST" && path === BASE) return { job_id: DEMO_ARCHIVE_DRY_JOB_ID, status: "queued" };
      return undefined;
    });
    renderWithClient(<ChangeArchiveTab />);
    fireEvent.click(await screen.findByTestId("archive-dry-run"));
    expect(await screen.findByTestId("archive-joined")).toBeInTheDocument();
  });

  it("does not call a fresh run joined", async () => {
    serveChangeAdmin();
    renderWithClient(<ChangeArchiveTab />);
    fireEvent.click(await screen.findByTestId("archive-dry-run"));
    await screen.findByTestId("archive-result");
    expect(screen.queryByTestId("archive-joined")).toBeNull();
  });

  it("explains why the archive cannot start", async () => {
    serveChangeAdmin([], (method, path) =>
      method === "GET" && path === BASE
        ? status({ can_start: false, blocked_code: "legacy_archive_no_tables", blocked_message: "In der Znuny-Datenbank ist kein Change-Management installiert." })
        : undefined,
    );
    renderWithClient(<ChangeArchiveTab />);
    expect(await screen.findByTestId("archive-blocked")).toHaveTextContent(
      "In der Znuny-Datenbank ist kein Change-Management (ITSMChangeManagement) installiert.",
    );
    expect(screen.getByTestId("archive-run")).toBeDisabled();
    expect(screen.getByTestId("archive-dry-run")).toBeDisabled();
  });
});
