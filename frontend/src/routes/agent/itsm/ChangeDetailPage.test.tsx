import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, makeChange, renderWithClient } from "@/components/itsm/change/changeTestSupport";
import { ChangeDetailPage } from "./ChangeDetailPage";

const { get, record, params } = vi.hoisted(() => ({ get: vi.fn(), record: vi.fn(), params: { ticketId: "130" } }));
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>("@tanstack/react-router");
  return { ...actual, useParams: () => params };
});
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, get } };
});
vi.mock("@/components/itsm/change/ChangeRecord", () => ({
  ChangeRecord: (props: { embedApprovals: boolean }) => {
    record(props);
    return <div data-testid="change-record-stub" />;
  },
}));

describe("ChangeDetailPage", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    get.mockReset();
    record.mockReset();
    params.ticketId = "130";
  });
  afterEach(cleanup);

  it("shows number, title, type and risk and the record with the approval UI", async () => {
    get.mockResolvedValue(makeChange({ change_type: "emergency", effective_risk_level: "high" }));
    renderWithClient(<ChangeDetailPage />);
    const header = await screen.findByTestId("change-detail-header");
    expect(header).toHaveTextContent("20261012130");
    expect(header).toHaveTextContent("Switch-Firmware aktualisieren");
    expect(screen.getByTestId("change-type-badge")).toHaveTextContent("Notfall-Change");
    expect(screen.getByTestId("risk-badge")).toHaveTextContent("Hoch");
    expect(screen.getByTestId("change-detail-ticket")).toHaveAttribute("href", "/agent/tickets/130");
    expect(screen.getByTestId("change-record-stub")).toBeInTheDocument();
    expect(record).toHaveBeenCalledWith(expect.objectContaining({ embedApprovals: true }));
    expect(get).toHaveBeenCalledWith(130, expect.anything());
  });

  it("points to the Znuny changes in Mode A", async () => {
    get.mockRejectedValue(changeError(409, { code: "change_mode_a" }));
    renderWithClient(<ChangeDetailPage />);
    expect(await screen.findByTestId("change-detail-error")).toHaveTextContent("Modus A");
    expect(screen.getByTestId("change-detail-legacy")).toHaveAttribute("href", "/agent/itsm/legacy-changes?ticket_id=130");
  });

  it("says when the change does not exist or cannot be read", async () => {
    get.mockRejectedValue(changeError(404, { code: "change_not_found" }));
    renderWithClient(<ChangeDetailPage />);
    expect(await screen.findByTestId("change-detail-error")).toHaveTextContent("Den Change gibt es nicht");
    expect(screen.queryByTestId("change-detail-legacy")).toBeNull();
  });

  it("refuses an id that is no number without asking the API", async () => {
    params.ticketId = "abc";
    renderWithClient(<ChangeDetailPage />);
    expect(await screen.findByTestId("change-detail-error")).toBeInTheDocument();
    await waitFor(() => expect(get).not.toHaveBeenCalled());
  });
});
