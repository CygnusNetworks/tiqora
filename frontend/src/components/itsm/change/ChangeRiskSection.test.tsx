import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, makeChange, makeRisk, renderWithClient } from "./changeTestSupport";
import { ChangeRiskSection } from "./ChangeRiskSection";

const { risk, saveRiskAnswers, evaluateRisk, overrideRisk } = vi.hoisted(() => ({
  risk: vi.fn(),
  saveRiskAnswers: vi.fn(),
  evaluateRisk: vi.fn(),
  overrideRisk: vi.fn(),
}));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, risk, saveRiskAnswers, evaluateRisk, overrideRisk } };
});

const finding = (over: Partial<ReturnType<typeof makeRisk>["findings"][number]>) => ({
  check_key: "core.open_incidents",
  severity: "warn" as const,
  points: 4,
  message_key: "itsm.change.risk.findings.open_incidents",
  params: { count: 3, lookback_days: 14 },
  refs: [],
  hidden_ref_count: 0,
  ...over,
});

describe("ChangeRiskSection", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    for (const f of [risk, saveRiskAnswers, evaluateRisk, overrideRisk]) f.mockReset();
  });
  afterEach(cleanup);

  it("shows level, score and the thresholds", async () => {
    risk.mockResolvedValue(makeRisk());
    renderWithClient(<ChangeRiskSection change={makeChange()} />);
    const summary = await screen.findByTestId("change-risk-summary");
    expect(within(summary).getByTestId("risk-badge")).toHaveTextContent("Mittel");
    expect(summary).toHaveTextContent("12 Punkte");
    expect(summary).toHaveTextContent("Mittel ab 10, Hoch ab 20, Sehr hoch ab 35");
  });

  it("saves the answers of the questionnaire", async () => {
    risk.mockResolvedValue(makeRisk());
    saveRiskAnswers.mockResolvedValue(makeRisk({ answers: { tested: "no" } }));
    renderWithClient(<ChangeRiskSection change={makeChange()} />);
    fireEvent.click(await screen.findByTestId("change-risk-answer-tested"));
    fireEvent.click(await screen.findByTestId("change-risk-answer-tested-menu-option-no"));
    fireEvent.click(screen.getByTestId("change-risk-answers-save"));
    await waitFor(() => expect(saveRiskAnswers).toHaveBeenCalledWith(130, { tested: "no" }));
  });

  it("evaluates again on request", async () => {
    risk.mockResolvedValue(makeRisk());
    evaluateRisk.mockResolvedValue(makeRisk({ score: 20, level: "high", effective_level: "high" }));
    renderWithClient(<ChangeRiskSection change={makeChange()} />);
    fireEvent.click(await screen.findByTestId("change-risk-evaluate"));
    await waitFor(() => expect(evaluateRisk).toHaveBeenCalledWith(130));
  });

  it("links only the readable references of a finding and counts the rest", async () => {
    risk.mockResolvedValue(
      makeRisk({
        findings: [
          finding({ refs: ["ticket:100", "ticket:102"], hidden_ref_count: 2 }),
          finding({ check_key: "core.ci_incident", message_key: "itsm.change.risk.findings.ci_incident", params: { incident: 1, warning: 0 }, refs: ["ci:201"] }),
          finding({ check_key: "core.freeze", message_key: "itsm.change.risk.findings.freeze", params: { count: 1, blocking: 1, truncated: 0 }, severity: "block", refs: ["freeze:1"] }),
        ],
      }),
    );
    renderWithClient(<ChangeRiskSection change={makeChange()} />);
    const rows = await screen.findAllByTestId("change-risk-finding");
    expect(rows[0]).toHaveTextContent("3 offene Incidents auf den betroffenen Services in den letzten 14 Tagen");
    expect(within(rows[0]).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/agent/tickets/100", "/agent/tickets/102"]);
    expect(within(rows[0]).getByTestId("change-risk-hidden-refs")).toHaveTextContent("2 weitere ohne Leserecht");
    expect(within(rows[1]).getByRole("link")).toHaveAttribute("href", "/agent/cmdb/201");
    // a freeze window has no agent page: named, not linked
    expect(within(rows[2]).queryByRole("link")).toBeNull();
    expect(rows[2]).toHaveTextContent("Sperrzeitraum #1");
  });

  it("marks a finding whose list the backend cut off", async () => {
    risk.mockResolvedValue(
      makeRisk({ findings: [finding({ check_key: "core.overlap", message_key: "itsm.change.risk.findings.overlap", params: { count: 0, truncated: 1 } })] }),
    );
    renderWithClient(<ChangeRiskSection change={makeChange()} />);
    expect(await screen.findByTestId("change-risk-finding")).toHaveTextContent("Viele andere Changes im selben Zeitfenster; nicht alle konnten geprüft werden");
  });

  it("has a text for every finding the core checks emit", async () => {
    const keys = ["service_criticality", "ci_incident", "open_incidents", "overlap", "freeze", "freeze_truncated", "failed_history", "emergency", "missing_backout", "check_failed"];
    // the API sends the params with the key; counted findings resolve to their plural forms
    for (const lng of ["de", "en"]) {
      for (const key of keys) expect(i18n.exists(`itsm.change.risk.findings.${key}`, { lng, count: 2 }), `${lng}: ${key}`).toBe(true);
    }
  });

  it("offers the override only to a manager and sends level and reason", async () => {
    risk.mockResolvedValue(makeRisk({ can_override: true }));
    overrideRisk.mockResolvedValue(makeRisk({ can_override: true, override_level: "low", effective_level: "low" }));
    renderWithClient(<ChangeRiskSection change={makeChange()} />);
    fireEvent.click(await screen.findByTestId("change-risk-override-open"));
    fireEvent.click(screen.getByTestId("change-risk-override-level"));
    fireEvent.click(await screen.findByTestId("change-risk-override-level-menu-option-low"));
    expect(screen.getByTestId("change-risk-override-save")).toBeDisabled();
    fireEvent.change(screen.getByTestId("change-risk-override-reason"), { target: { value: "Bereits zweimal erprobt" } });
    fireEvent.click(screen.getByTestId("change-risk-override-save"));
    await waitFor(() => expect(overrideRisk).toHaveBeenCalledWith(130, { level: "low", reason: "Bereits zweimal erprobt" }));
  });

  it("hides the override from other agents and shows an existing override", async () => {
    risk.mockResolvedValue(makeRisk({ override_level: "high", override_reason: "CAB-Beschluss", effective_level: "high" }));
    renderWithClient(<ChangeRiskSection change={makeChange()} />);
    expect(await screen.findByTestId("change-risk-override")).toHaveTextContent("CAB-Beschluss");
    expect(screen.queryByTestId("change-risk-override-open")).toBeNull();
  });

  it("names a refused override", async () => {
    risk.mockResolvedValue(makeRisk({ can_override: true }));
    overrideRisk.mockRejectedValue(changeError(422, { code: "risk_override_reason_required" }));
    renderWithClient(<ChangeRiskSection change={makeChange()} />);
    fireEvent.click(await screen.findByTestId("change-risk-override-open"));
    fireEvent.change(screen.getByTestId("change-risk-override-reason"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("change-risk-override-save"));
    expect(await screen.findByTestId("change-risk-error")).toHaveTextContent("Bitte begründen Sie die Änderung der Risikostufe.");
  });
});
