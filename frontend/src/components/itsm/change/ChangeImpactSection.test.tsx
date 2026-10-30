import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import i18n from "@/i18n";
import type { Job } from "@/lib/jobsApi";
import { changeError, makeChange, makeImpact, renderWithClient } from "./changeTestSupport";
import { ChangeImpactSection } from "./ChangeImpactSection";

const { impact, recomputeImpact, jobGet } = vi.hoisted(() => ({ impact: vi.fn(), recomputeImpact: vi.fn(), jobGet: vi.fn() }));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, impact, recomputeImpact } };
});
vi.mock("@/lib/jobsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/jobsApi")>("@/lib/jobsApi");
  return { ...actual, jobsApi: { ...actual.jobsApi, get: jobGet } };
});

const filled = makeImpact({
  services: [
    { service_id: 1, name: "E-Mail", incident_functionality: "warning", direct: true },
    { service_id: 3, name: "Arbeitsplatz", incident_functionality: null, direct: false },
  ],
  cis: [
    {
      ref: "tiqora:201", ci_id: 201, number: "1002001", name: "srv-web-01.example.org", class_key: "computer",
      class_name: "Computer", incident_state: "Betriebsbereit", incident_functionality: "operational", direct: true,
    },
  ],
  hidden_ci_count: 2,
  hidden_service_count: 1,
  incidents: [{ ticket_id: 100, tn: "20261012000", title: "Website nicht erreichbar", queue_id: 1, state: "open", service_id: 1 }],
});

const job = (status: Job["status"]): Job => ({
  id: 9461, kind: "core.itsm_change_impact", status, title_key: "jobs.kinds.core.itsm_change_impact",
  progress_done: 0, progress_total: null, progress_message: null, cancel_requested: false, error: null,
  result: null, files: [], created_by: 1, created_at: "2026-10-12T08:00:00Z", started_at: null, finished_at: null,
});

describe("ChangeImpactSection", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    for (const f of [impact, recomputeImpact, jobGet]) f.mockReset();
  });
  afterEach(cleanup);

  it("lists services, CIs and open incidents with links and counts what cannot be read", async () => {
    impact.mockResolvedValue(filled);
    renderWithClient(<ChangeImpactSection change={makeChange()} />);
    const services = await screen.findByTestId("change-impact-services");
    expect(within(services).getAllByTestId("change-impact-service").map((s) => s.textContent)).toEqual([
      expect.stringContaining("E-Mail"),
      expect.stringContaining("Arbeitsplatz"),
    ]);
    expect(within(services).getByTestId("change-impact-hidden-services")).toHaveTextContent("1 weitere ohne Leserecht");
    const cis = screen.getByTestId("change-impact-cis");
    expect(within(cis).getByRole("link", { name: /srv-web-01/ })).toHaveAttribute("href", "/agent/cmdb/201");
    expect(within(cis).getByTestId("change-impact-hidden-cis")).toHaveTextContent("2 weitere ohne Leserecht");
    expect(within(screen.getByTestId("change-impact-incidents")).getByRole("link", { name: /Website nicht erreichbar/ })).toHaveAttribute(
      "href",
      "/agent/tickets/100",
    );
  });

  it("says when nothing is computed yet or the set is out of date", async () => {
    impact.mockResolvedValue(makeImpact({ computed_at: null, requested_at: null, stale: true }));
    renderWithClient(<ChangeImpactSection change={makeChange()} />);
    expect(await screen.findByTestId("change-impact-stale")).toHaveTextContent("noch nicht berechnet");
  });

  it("says the impact is being computed while the creation's job runs", async () => {
    impact.mockResolvedValue(makeImpact({ computed_at: null, requested_at: "2026-10-12T08:00:00Z", stale: true }));
    renderWithClient(<ChangeImpactSection change={makeChange()} />);
    expect(await screen.findByTestId("change-impact-stale")).toHaveTextContent("Die Auswirkung wird berechnet");
  });

  it("recomputes in the background and reloads when the job is done", async () => {
    impact.mockResolvedValue(filled);
    recomputeImpact.mockResolvedValue({ job_id: 9461, status: "queued" });
    jobGet.mockResolvedValue(job("succeeded"));
    renderWithClient(<ChangeImpactSection change={makeChange()} />);
    await screen.findByTestId("change-impact-services");
    expect(impact).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("change-impact-recompute"));
    await waitFor(() => expect(recomputeImpact).toHaveBeenCalledWith(130));
    expect(await screen.findByTestId("change-impact-job")).toBeInTheDocument();
    await waitFor(() => expect(impact).toHaveBeenCalledTimes(2));
  });

  it("offers no recompute on a final change or without write access", async () => {
    impact.mockResolvedValue(filled);
    renderWithClient(<ChangeImpactSection change={makeChange({ phase: "closed" })} />);
    await screen.findByTestId("change-impact-services");
    expect(screen.queryByTestId("change-impact-recompute")).toBeNull();
    cleanup();
    renderWithClient(<ChangeImpactSection change={makeChange({ can_write: false })} />);
    await screen.findByTestId("change-impact-services");
    expect(screen.queryByTestId("change-impact-recompute")).toBeNull();
  });

  it("names a CMDB that is not there", async () => {
    impact.mockRejectedValue(changeError(409, { code: "cmdb_unavailable" }));
    renderWithClient(<ChangeImpactSection change={makeChange()} />);
    expect(await screen.findByTestId("change-impact-error")).toHaveTextContent("Die CMDB ist nicht verfügbar");
  });
});
