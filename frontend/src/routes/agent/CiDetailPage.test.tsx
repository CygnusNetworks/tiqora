import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { makeCi } from "@/components/itsm/testSupport";
import { CiDetailPage } from "./CiDetailPage";

const { ci, ciVersions, ciTickets, params } = vi.hoisted(() => ({
  ci: vi.fn(),
  ciVersions: vi.fn(),
  ciTickets: vi.fn(),
  params: { current: { source: "znuny", ciId: "201" } },
}));
vi.mock("@tanstack/react-router", () => ({ useParams: () => params.current, useRouter: () => null }));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, ci, ciVersions, ciTickets } };
});

const OPERATIONAL = { key: "41", name: "Betriebsbereit", functionality: "operational" };
const INCIDENT = { key: "43", name: "Störung", functionality: "incident" };
const version = (id: number, seq: number, name: string, inci = OPERATIONAL) => ({
  id,
  seq,
  name,
  deployment_state: null,
  incident_state: inci,
  created_at: "2026-09-01T08:00:00Z",
  created_by: 1,
  created_by_name: "Alex Turner",
  change_note: seq === 2 ? "RAM erweitert" : null,
});
const VERSIONS = [version(903, 3, "srv-web-01.example.org", INCIDENT), version(902, 2, "srv-old-name.example.org"), version(901, 1, "srv-first.example.org")];

function detail(v: (typeof VERSIONS)[number], attrs = [{ key: "RAM", label: "RAM", kind: "text", display: `${v.seq * 8} GB`, raw: null, ref: null, children: [] }]) {
  return {
    summary: makeCi(201, { edit_url: "https://znuny.example.org/index.pl?ConfigItemID=201" }),
    version: v,
    attributes: attrs,
    definition_version: 1,
    read_only: true,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CiDetailPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  params.current = { source: "znuny", ciId: "201" };
  ci.mockReset().mockImplementation(async (_s: string, _id: number, versionId?: number | null) =>
    detail(VERSIONS.find((v) => v.id === versionId) ?? VERSIONS[0]),
  );
  ciVersions.mockReset().mockResolvedValue({ items: VERSIONS, total: 3 });
  ciTickets.mockReset().mockResolvedValue([
    {
      link_type: "RelevantTo",
      source_key: "Ticket",
      target_key: "ITSMConfigItem",
      state: "open",
      other_ticket_id: 100,
      other_tn: "20260701000",
      other_title: "Website nicht erreichbar",
      other_state: "open",
      other_state_type: "open",
      other_role: "incident",
    },
  ]);
});
afterEach(cleanup);

describe("CiDetailPage", () => {
  it("shows number, name, class, both states and the attribute tree", async () => {
    renderPage();
    expect(await screen.findByTestId("ci-detail-number")).toHaveTextContent("100200201");
    expect(screen.getByTestId("ci-detail-name")).toHaveTextContent("srv-web-01.example.org");
    const header = screen.getByTestId("ci-detail-header");
    expect(header).toHaveTextContent("Computer");
    expect(within(header).getAllByTestId("ci-state-badge")).toHaveLength(2);
    expect(screen.getByTestId("ci-detail-attributes")).toHaveTextContent("24 GB");
    expect(screen.getByTestId("ci-detail-readonly")).toBeInTheDocument();
  });

  it("badges only the current version and loads an older one on 'View version'", async () => {
    renderPage();
    await screen.findByTestId("ci-version-903");
    expect(screen.getByTestId("ci-version-current-903")).toHaveTextContent("Current version");
    expect(screen.queryByTestId("ci-version-current-902")).toBeNull();
    expect(screen.queryByTestId("ci-version-view-903")).toBeNull();
    expect(screen.queryByTestId("ci-detail-old-version")).toBeNull();

    fireEvent.click(screen.getByTestId("ci-version-view-902"));
    await waitFor(() => expect(ci).toHaveBeenLastCalledWith("znuny", 201, 902, expect.anything()));
    expect(await screen.findByTestId("ci-detail-old-version")).toHaveTextContent("Viewing version 2");
    expect(screen.getByTestId("ci-detail-name")).toHaveTextContent("srv-old-name.example.org");
    expect(screen.getByTestId("ci-detail-attributes")).toHaveTextContent("16 GB");
    expect(screen.getByTestId("ci-version-902")).toHaveTextContent("Shown");

    fireEvent.click(screen.getByTestId("ci-detail-current"));
    await waitFor(() => expect(screen.queryByTestId("ci-detail-old-version")).toBeNull());
    expect(ci).toHaveBeenLastCalledWith("znuny", 201, null, expect.anything());
    expect(screen.getByTestId("ci-detail-name")).toHaveTextContent("srv-web-01.example.org");
  });

  it("lists linked tickets with a link to the ticket", async () => {
    renderPage();
    const row = await screen.findByTestId("ci-ticket-100");
    expect(row).toHaveTextContent("Website nicht erreichbar");
    expect(within(row).getByRole("link")).toHaveAttribute("href", "/agent/tickets/100");
  });

  it("offers 'Edit in Znuny' in Mode A with a safe URL only", async () => {
    renderPage();
    const link = await screen.findByTestId("ci-detail-edit");
    expect(link).toHaveAttribute("href", "https://znuny.example.org/index.pl?ConfigItemID=201");
    cleanup();
    ci.mockResolvedValue({ ...detail(VERSIONS[0]), summary: makeCi(201, { edit_url: "javascript:alert(1)" }) });
    renderPage();
    await screen.findByTestId("ci-detail-name");
    expect(screen.queryByTestId("ci-detail-edit")).toBeNull();
  });

  it("renders no edit link when the CI is not read-only", async () => {
    ci.mockResolvedValue({ ...detail(VERSIONS[0]), read_only: false });
    renderPage();
    await screen.findByTestId("ci-detail-name");
    expect(screen.queryByTestId("ci-detail-edit")).toBeNull();
  });

  it("says so for a CI that is unknown or not readable", async () => {
    ci.mockRejectedValue(new ApiError(404, { detail: { code: "ci_not_found" } }, "/x"));
    renderPage();
    expect(await screen.findByTestId("ci-detail-error")).toHaveTextContent("does not exist");
  });

  it("explains an unavailable CMDB in plain language", async () => {
    ci.mockRejectedValue(new ApiError(409, { detail: { code: "cmdb_not_installed" } }, "/x"));
    renderPage();
    expect(await screen.findByTestId("ci-detail-error")).toHaveTextContent("The Znuny CMDB is not installed.");
  });
});
