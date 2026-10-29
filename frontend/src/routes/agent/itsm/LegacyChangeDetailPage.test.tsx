import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, within } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, renderWithClient } from "@/components/itsm/change/changeTestSupport";
import type { LegacyChange } from "@/lib/changeApi";
import { LegacyChangeDetailPage } from "./LegacyChangeDetailPage";

const { legacy, params } = vi.hoisted(() => ({ legacy: vi.fn(), params: { changeId: "11" } }));
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>("@tanstack/react-router");
  return { ...actual, useParams: () => params };
});
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, legacy } };
});

const makeLegacy = (over: Partial<LegacyChange> = {}): LegacyChange => ({
  source: "live",
  znuny_change_id: 11,
  number: "2024040110000012",
  title: "Core-Switch ersetzen",
  state: "successful",
  category: "3 normal",
  impact: "4 high",
  priority: "4 high",
  manager_login: "aturner",
  builder_login: "mbeispiel",
  requested_time: null,
  planned_start: "2024-04-06T20:00:00Z",
  planned_end: "2024-04-07T02:00:00Z",
  actual_start: "2024-04-06T20:10:00Z",
  actual_end: null,
  created_at: "2024-03-20T08:00:00Z",
  changed_at: "2024-04-07T02:00:00Z",
  description: "\nAustausch des Core-Switches.\n",
  justification: "Ende der Herstellerunterstützung.",
  dynamic_fields: { Standort: ["Rechenzentrum"], Leer: [] },
  cab: [
    { kind: "user", login: "aturner" },
    { kind: "customer_user", login: "kunde@example.org" },
  ],
  workorders: [
    {
      number: 1,
      title: "Konfiguration sichern",
      work_order_type: "backup",
      state: "closed",
      agent_login: "mbeispiel",
      planned_start: "2024-04-06T20:00:00Z",
      planned_end: "2024-04-06T21:00:00Z",
      actual_start: null,
      actual_end: null,
      planned_effort: 1,
      accounted_time: 0.7,
      instruction: "Konfiguration exportieren.",
      report: "Erledigt.",
      dynamic_fields: {},
    },
  ],
  tickets: [{ ticket_id: 100, tn: "20261012000", title: "Website nicht erreichbar", state: "closed successful", link_type: "RelevantTo", direction: "source", workorder_number: 0 }],
  cis: [
    { ref: "znuny:202", number: "1002002", name: "sw-core-01.example.org", class_name: "Netzwerk", link_type: "RelevantTo", direction: "source", workorder_number: 1 },
    { ref: "tiqora:5", number: null, name: "fw-01.example.org", class_name: null, link_type: "RelevantTo", direction: "source", workorder_number: 0 },
  ],
  services: [{ service_id: 1, name: "E-Mail", link_type: "RelevantTo", direction: "source", workorder_number: 1 }],
  hidden_links: 2,
  history: [
    { created_at: "2024-03-20T08:00:00Z", history_type: "ChangeAdd", field: null, old: null, new: null, old_display: null, new_display: null, user_login: "mbeispiel", workorder_number: 0 },
    { created_at: "2024-04-07T02:00:00Z", history_type: "ChangeUpdate", field: "ChangeStateID", old: "9", new: "12", old_display: "in progress", new_display: "successful", user_login: "aturner", workorder_number: 0 },
    { created_at: "2024-04-07T03:00:00Z", history_type: "ChangeUpdate", field: "Title", old: "a &amp; b", new: "<img src=x onerror=alert(1)>", old_display: null, new_display: null, user_login: null, workorder_number: 1 },
  ],
  znuny_url: "https://znuny.example.org/znuny/index.pl?Action=AgentITSMChangeZoom;ChangeID=11",
  ...over,
});

describe("LegacyChangeDetailPage", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    legacy.mockReset();
    params.changeId = "11";
  });
  afterEach(cleanup);

  it("shows data, times, CAB, work orders and history of a live change", async () => {
    legacy.mockResolvedValue(makeLegacy());
    renderWithClient(<LegacyChangeDetailPage />);
    const header = await screen.findByTestId("legacy-detail-header");
    expect(header).toHaveTextContent("2024040110000012");
    expect(header).toHaveTextContent("Core-Switch ersetzen");
    expect(screen.getByTestId("legacy-source-badge")).toHaveTextContent("Live aus Znuny");
    expect(screen.getByTestId("legacy-fact-state")).toHaveTextContent("successful");
    expect(screen.getByTestId("legacy-fact-manager")).toHaveTextContent("aturner");
    expect(screen.getByTestId("legacy-time-plannedStart")).toHaveTextContent("2024-04-06 20:00 UTC");
    expect(screen.getByTestId("legacy-time-actualEnd")).toHaveTextContent("–");
    expect(screen.getByTestId("legacy-description").textContent).toBe("Austausch des Core-Switches.");
    expect(screen.getAllByTestId("legacy-cab-member").map((e) => e.textContent)).toEqual(["aturner", "kunde@example.org (Kunde)"]);
    const wo = screen.getByTestId("legacy-workorder-1");
    expect(wo).toHaveTextContent("Konfiguration sichern");
    expect(wo).toHaveTextContent("Aufwand 1 h geplant, 0.7 h gebucht");
    expect(wo).toHaveTextContent("Anweisung: Konfiguration exportieren.");
    expect(wo).toHaveTextContent("Bericht: Erledigt.");
    expect(screen.getByTestId("legacy-detail-dynamic-fields")).toHaveTextContent("Standort");
    expect(screen.getByTestId("legacy-detail-dynamic-fields")).not.toHaveTextContent("Leer");
    const history = screen.getAllByTestId("legacy-history-entry");
    expect(history).toHaveLength(3);
    expect(history[1]).toHaveTextContent("ChangeStateID: in progress → successful");
  });

  it("offers 'In Znuny bearbeiten' in live mode only", async () => {
    legacy.mockResolvedValue(makeLegacy());
    const { unmount } = renderWithClient(<LegacyChangeDetailPage />);
    const link = await screen.findByTestId("legacy-detail-znuny-link");
    expect(link).toHaveTextContent("In Znuny bearbeiten");
    expect(link).toHaveAttribute("href", expect.stringContaining("ChangeID=11"));
    unmount();
    legacy.mockResolvedValue(makeLegacy({ source: "archive", znuny_url: null }));
    renderWithClient(<LegacyChangeDetailPage />);
    expect(await screen.findByTestId("legacy-source-badge")).toHaveTextContent("Archiv");
    expect(screen.queryByTestId("legacy-detail-znuny-link")).toBeNull();
    expect(screen.getByTestId("legacy-detail-archive-hint")).toBeInTheDocument();
  });

  it("does not link a Znuny address that is not http(s)", async () => {
    legacy.mockResolvedValue(makeLegacy({ znuny_url: "javascript:alert(1)" }));
    renderWithClient(<LegacyChangeDetailPage />);
    await screen.findByTestId("legacy-detail-header");
    expect(screen.queryByTestId("legacy-detail-znuny-link")).toBeNull();
  });

  it("does not offer 'In Znuny bearbeiten' for a live change without a Znuny address", async () => {
    legacy.mockResolvedValue(makeLegacy({ znuny_url: null }));
    renderWithClient(<LegacyChangeDetailPage />);
    await screen.findByTestId("legacy-detail-header");
    expect(screen.queryByTestId("legacy-detail-znuny-link")).toBeNull();
  });

  it("links tickets and CIs (via ciDetailHref) and counts the links without read access", async () => {
    legacy.mockResolvedValue(makeLegacy());
    renderWithClient(<LegacyChangeDetailPage />);
    const links = await screen.findByTestId("legacy-detail-links");
    expect(within(screen.getByTestId("legacy-ticket-100")).getByRole("link")).toHaveAttribute("href", "/agent/tickets/100");
    expect(within(screen.getByTestId("legacy-ci-znuny:202")).getByRole("link")).toHaveAttribute("href", "/agent/itsm/cis/znuny/202");
    expect(within(screen.getByTestId("legacy-ci-znuny:202")).getByRole("link")).toHaveTextContent("1002002 sw-core-01.example.org");
    expect(within(screen.getByTestId("legacy-ci-tiqora:5")).getByRole("link")).toHaveAttribute("href", "/agent/cmdb/5");
    expect(screen.getByTestId("legacy-service-1")).toHaveTextContent("E-Mail");
    expect(screen.getByTestId("legacy-service-1")).toHaveTextContent("am Arbeitsauftrag 1");
    expect(screen.getByTestId("legacy-hidden-links")).toHaveTextContent("2 weitere ohne Leserecht");
    expect(links).toHaveTextContent("am Change");
  });

  it("shows a CI of the archive that has no Tiqora page as text", async () => {
    legacy.mockResolvedValue(makeLegacy({ source: "archive", znuny_url: null }));
    renderWithClient(<LegacyChangeDetailPage />);
    const ci = await screen.findByTestId("legacy-ci-znuny:202");
    expect(within(ci).queryByRole("link")).toBeNull();
    expect(ci).toHaveTextContent("1002002 sw-core-01.example.org");
    expect(within(screen.getByTestId("legacy-ci-tiqora:5")).getByRole("link")).toHaveAttribute("href", "/agent/cmdb/5");
  });

  it("renders history values as plain text, never as html", async () => {
    legacy.mockResolvedValue(makeLegacy());
    const { container } = renderWithClient(<LegacyChangeDetailPage />);
    const entry = (await screen.findAllByTestId("legacy-history-change"))[1];
    expect(entry.textContent).toContain("Title: a &amp; b → <img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
  });

  it("says when the change does not exist", async () => {
    legacy.mockRejectedValue(changeError(404, { code: "legacy_change_not_found" }));
    renderWithClient(<LegacyChangeDetailPage />);
    expect(await screen.findByTestId("legacy-detail-error")).toHaveTextContent("Den Znuny-Change gibt es nicht.");
  });
});
