import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithClient } from "@/components/itsm/change/changeTestSupport";
import { resolveChangeData } from "@/demo/changeMock";
import { ApiError } from "@/lib/api";
import type { AdminCab } from "@/lib/changeAdminApi";
import { bodyOf, serveChangeAdmin, useGerman, type Recorded } from "./changeAdminTestUtils";
import { ChangeCabTab } from "./ChangeCabTab";

beforeAll(useGerman);
afterEach(() => vi.restoreAllMocks());

const BASE = "/api/v1/admin/itsm/change/cabs";
const demoCabs = () => structuredClone(resolveChangeData(BASE, "GET", "", "tiqora") as AdminCab[]);
const rowOf = async (id: number) =>
  (await screen.findAllByTestId("cab-row")).find((r) => r.getAttribute("data-cab-id") === String(id))!;

describe("ChangeCabTab", () => {
  it("lists the boards with rule, members and the emergency flag", async () => {
    serveChangeAdmin();
    renderWithClient(<ChangeCabTab />);
    const network = await rowOf(1);
    expect(network).toHaveTextContent("CAB Netzwerk");
    expect(within(network).getByTestId("cab-row-rule")).toHaveTextContent("Mehrheit");
    expect(within(network).getByTestId("cab-row-members")).toHaveTextContent("CAB Netzwerk (3 Personen)");
    expect(within(network).queryByTestId("cab-emergency")).toBeNull();
    const emergency = await rowOf(2);
    expect(within(emergency).getByTestId("cab-emergency")).toHaveTextContent("Notfall-CAB");
    expect(within(emergency).getByTestId("cab-row-members")).toHaveTextContent("Alex Turner, users (4 Personen)");
  });

  it("flags a member that no longer exists and a board without members", async () => {
    const cabs = demoCabs();
    cabs[0].members = [{ member_kind: "group", member_ref: "9", label: "Gruppe #9", count: null, missing: true }];
    cabs[1].members = [];
    serveChangeAdmin([], (method, path) => (method === "GET" && path === BASE ? cabs : undefined));
    renderWithClient(<ChangeCabTab />);
    expect(within(await rowOf(1)).getByTestId("cab-member-missing")).toHaveTextContent("fehlt");
    expect(within(await rowOf(2)).getByTestId("cab-row-members")).toHaveTextContent("Keine Mitglieder");
    expect(within(await rowOf(2)).getByTestId("cab-row-empty-hint")).toHaveTextContent("Ein CAB ohne Mitglieder kann keine Freigabe anfordern.");
  });

  it("edits the rule with a quorum and saves board and members", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(within(await rowOf(2)).getByTestId("cab-edit"));
    const dialog = await screen.findByTestId("cab-dialog");
    expect(within(dialog).getAllByTestId("cab-member-row")).toHaveLength(2);
    fireEvent.click(screen.getByTestId("cab-rule"));
    fireEvent.click(screen.getByTestId("cab-rule-menu-option-quorum"));
    fireEvent.click(screen.getByTestId("cab-save"));
    expect(await screen.findByTestId("cab-problems")).toHaveTextContent("Bitte geben Sie eine Mindestzahl zwischen 1 und 100 ein.");
    expect(requests.some((r) => r.method === "PUT")).toBe(false);
    fireEvent.change(screen.getByTestId("cab-quorum"), { target: { value: "1" } });
    fireEvent.click(screen.getByTestId("cab-save"));
    await waitFor(() => expect(screen.queryByTestId("cab-dialog")).toBeNull());
    expect(bodyOf(requests, "PUT", `${BASE}/2`)).toEqual({
      name: "Notfall-CAB",
      description: "Kleiner Kreis für Notfall-Changes.",
      rule: "quorum",
      quorum: 1,
      is_emergency: true,
      valid: true,
    });
    // The members did not change: they are not written again.
    expect(requests.some((r) => r.path === `${BASE}/2/members`)).toBe(false);
  });

  it("raises the quorum together with a new member: members first when the board needs them", async () => {
    const requests: Recorded[] = [];
    const cabs = demoCabs();
    cabs[0] = { ...cabs[0], rule: "quorum", quorum: 2, members: [
      { member_kind: "user", member_ref: "1", label: "Ada Agent", count: null, missing: false },
      { member_kind: "user", member_ref: "2", label: "Alex Turner", count: null, missing: false },
    ] };
    // The backend checks each half against the other half as stored (cab.update_cab / set_members).
    let stored = { quorum: 2, members: 2 };
    serveChangeAdmin(requests, (method, path, body) => {
      if (method === "GET" && path === BASE) return cabs;
      if (method === "PUT" && path === `${BASE}/1`) {
        const quorum = (body as { quorum: number }).quorum;
        if (quorum > stored.members) throw new ApiError(422, { detail: { code: "cab_invalid", reason: "quorum_exceeds_members", message: `step 'CAB': quorum ${quorum} exceeds distinct approvers` } }, path);
        stored = { ...stored, quorum };
        return cabs[0];
      }
      if (method === "PUT" && path === `${BASE}/1/members`) {
        stored = { ...stored, members: (body as unknown[]).length };
        return cabs[0];
      }
      return undefined;
    });
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(within(await rowOf(1)).getByTestId("cab-edit"));
    await screen.findByTestId("cab-dialog");
    fireEvent.change(screen.getByTestId("cab-quorum"), { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("cab-member-add"));
    fireEvent.click(screen.getByTestId("cab-member-ref-2"));
    fireEvent.click(await screen.findByTestId("cab-member-ref-2-menu-option-3"));
    fireEvent.click(screen.getByTestId("cab-save"));
    await waitFor(() => expect(screen.queryByTestId("cab-dialog")).toBeNull());
    expect(requests.filter((r) => r.method === "PUT").map((r) => r.path)).toEqual([`${BASE}/1`, `${BASE}/1/members`, `${BASE}/1`]);
    expect(stored).toEqual({ quorum: 3, members: 3 });
  });

  it("says that only the members were saved when the board fails after the members-first retry", async () => {
    const requests: Recorded[] = [];
    const cabs = demoCabs();
    cabs[0] = { ...cabs[0], rule: "quorum", quorum: 2, members: [
      { member_kind: "user", member_ref: "1", label: "Ada Agent", count: null, missing: false },
      { member_kind: "user", member_ref: "2", label: "Alex Turner", count: null, missing: false },
    ] };
    let boardWrites = 0;
    serveChangeAdmin(requests, (method, path) => {
      if (method === "GET" && path === BASE) return cabs;
      if (method === "PUT" && path === `${BASE}/1`) {
        boardWrites += 1;
        throw new ApiError(422, { detail: { code: "cab_invalid", reason: boardWrites === 1 ? "quorum_exceeds_members" : "invalid_definition" } }, path);
      }
      if (method === "PUT" && path === `${BASE}/1/members`) return cabs[0];
      return undefined;
    });
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(within(await rowOf(1)).getByTestId("cab-edit"));
    await screen.findByTestId("cab-dialog");
    fireEvent.change(screen.getByTestId("cab-quorum"), { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("cab-member-add"));
    fireEvent.click(screen.getByTestId("cab-member-ref-2"));
    fireEvent.click(await screen.findByTestId("cab-member-ref-2-menu-option-3"));
    fireEvent.click(screen.getByTestId("cab-save"));
    await waitFor(() => expect(screen.getByTestId("cab-error")).toHaveTextContent("Die Mitglieder sind gespeichert, Name und Regel jedoch nicht."));
    expect(requests.filter((r) => r.method === "PUT").map((r) => r.path)).toEqual([`${BASE}/1`, `${BASE}/1/members`, `${BASE}/1`]);
  });

  it("refuses a quorum above the number of individual members before saving", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(await screen.findByTestId("cab-new"));
    fireEvent.change(screen.getByTestId("cab-name"), { target: { value: "CAB Klein" } });
    fireEvent.click(screen.getByTestId("cab-rule"));
    fireEvent.click(screen.getByTestId("cab-rule-menu-option-quorum"));
    fireEvent.change(screen.getByTestId("cab-quorum"), { target: { value: "2" } });
    fireEvent.click(screen.getByTestId("cab-member-add"));
    fireEvent.click(screen.getByTestId("cab-member-ref-0"));
    fireEvent.click(await screen.findByTestId("cab-member-ref-0-menu-option-2"));
    fireEvent.click(screen.getByTestId("cab-save"));
    expect(await screen.findByTestId("cab-problems")).toHaveTextContent("Das Quorum ist größer als die Zahl der Mitglieder.");
    expect(requests.some((r) => r.method !== "GET")).toBe(false);
    // A group counts as unknown many: no client check.
    fireEvent.click(screen.getByTestId("cab-member-kind-0"));
    fireEvent.click(screen.getByTestId("cab-member-kind-0-menu-option-group"));
    fireEvent.click(screen.getByTestId("cab-member-ref-0"));
    fireEvent.click(await screen.findByTestId("cab-member-ref-0-menu-option-7"));
    fireEvent.click(screen.getByTestId("cab-save"));
    await waitFor(() => expect(screen.queryByTestId("cab-dialog")).toBeNull());
  });

  it("says that the board was saved when only its members were refused", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests, (method, path) => {
      if (method === "PUT" && path === `${BASE}/2/members`) throw new ApiError(422, { detail: { code: "cab_customer_members_disabled" } }, path);
      return undefined;
    });
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(within(await rowOf(2)).getByTestId("cab-edit"));
    await screen.findByTestId("cab-dialog");
    fireEvent.click(screen.getByTestId("cab-member-remove-1"));
    fireEvent.click(screen.getByTestId("cab-save"));
    const error = await screen.findByTestId("cab-error");
    expect(error).toHaveTextContent("Name und Regel sind gespeichert, die Mitglieder aber nicht.");
    expect(error).toHaveTextContent("Kunden als Freigeber sind ausgeschaltet");
  });

  it("creates a board, then stores its members on the new id", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(await screen.findByTestId("cab-new"));
    fireEvent.change(screen.getByTestId("cab-name"), { target: { value: "CAB Anwendungen" } });
    fireEvent.click(screen.getByTestId("cab-member-add"));
    fireEvent.click(screen.getByTestId("cab-member-ref-0"));
    fireEvent.click(await screen.findByTestId("cab-member-ref-0-menu-option-2"));
    fireEvent.click(screen.getByTestId("cab-save"));
    await waitFor(() => expect(screen.queryByTestId("cab-dialog")).toBeNull());
    expect(bodyOf(requests, "POST", BASE)).toEqual({
      name: "CAB Anwendungen",
      description: null,
      rule: "majority",
      quorum: null,
      is_emergency: false,
      valid: true,
    });
    // The demo answers the create with CAB 1.
    expect(bodyOf(requests, "PUT", `${BASE}/1/members`)).toEqual([{ member_kind: "user", member_ref: "2" }]);
  });

  it("keeps the dialog open on the created board when its members are refused", async () => {
    const requests: Recorded[] = [];
    let refuse = true;
    serveChangeAdmin(requests, (method, path) => {
      if (method === "PUT" && path === `${BASE}/1/members` && refuse) {
        throw new ApiError(422, { detail: { code: "list_not_found" } }, path);
      }
      return undefined;
    });
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(await screen.findByTestId("cab-new"));
    fireEvent.change(screen.getByTestId("cab-name"), { target: { value: "CAB Anwendungen" } });
    fireEvent.click(screen.getByTestId("cab-member-add"));
    fireEvent.click(screen.getByTestId("cab-member-kind-0"));
    fireEvent.click(screen.getByTestId("cab-member-kind-0-menu-option-list"));
    fireEvent.click(screen.getByTestId("cab-member-ref-0"));
    fireEvent.click(await screen.findByTestId("cab-member-ref-0-menu-option-1"));
    fireEvent.click(screen.getByTestId("cab-save"));
    const error = await screen.findByTestId("cab-error");
    expect(error).toHaveTextContent("Das CAB ist angelegt, die Mitglieder sind aber noch nicht gespeichert.");
    expect(error).toHaveTextContent("Eine Freigabegruppe des CAB gibt es nicht mehr.");
    refuse = false;
    fireEvent.click(screen.getByTestId("cab-save"));
    await waitFor(() => expect(screen.queryByTestId("cab-dialog")).toBeNull());
    expect(requests.filter((r) => r.method === "POST" && r.path === BASE)).toHaveLength(1);
    expect(bodyOf(requests, "PUT", `${BASE}/1`)).toMatchObject({ name: "CAB Anwendungen" });
  });

  it("offers no customer users and no customer-yielding rules while customers may not approve", async () => {
    serveChangeAdmin();
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(within(await rowOf(2)).getByTestId("cab-edit"));
    expect(await screen.findByTestId("cab-customers-off")).toHaveTextContent("Kundenbenutzer als CAB-Mitglieder");
    fireEvent.click(screen.getByTestId("cab-member-kind-0"));
    expect(screen.getByTestId("cab-member-kind-0-menu-option-resolver")).toBeInTheDocument();
    expect(screen.queryByTestId("cab-member-kind-0-menu-option-customer_user")).toBeNull();
    fireEvent.click(screen.getByTestId("cab-member-kind-0-menu-option-resolver"));
    fireEvent.click(screen.getByTestId("cab-member-ref-0"));
    expect(await screen.findByTestId("cab-member-ref-0-menu-option-ticket.owner")).toBeInTheDocument();
    expect(screen.queryByTestId("cab-member-ref-0-menu-option-ticket.customer_user")).toBeNull();
  });

  it("offers customer users once customers may approve", async () => {
    const cabs = demoCabs().map((c) => ({ ...c, customer_members_allowed: true }));
    serveChangeAdmin([], (method, path) => (method === "GET" && path === BASE ? cabs : undefined));
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(within(await rowOf(2)).getByTestId("cab-edit"));
    await screen.findByTestId("cab-dialog");
    expect(screen.queryByTestId("cab-customers-off")).toBeNull();
    fireEvent.click(screen.getByTestId("cab-member-kind-0"));
    expect(screen.getByTestId("cab-member-kind-0-menu-option-customer_user")).toBeInTheDocument();
  });

  it("takes the customer setting from the approvals settings, also with no CAB yet", async () => {
    serveChangeAdmin([], (method, path) => {
      if (method === "GET" && path === BASE) return [];
      if (path === "/api/v1/admin/settings-sections/approvals") return { key: "approvals", values: { customer_approvers_enabled: true } };
      return undefined;
    });
    renderWithClient(<ChangeCabTab />);
    await waitFor(() => expect(screen.getByTestId("cabs-empty")).toHaveTextContent("Es gibt noch kein CAB."));
    fireEvent.click(screen.getByTestId("cab-new"));
    await screen.findByTestId("cab-dialog");
    expect(screen.queryByTestId("cab-customers-off")).toBeNull();
    fireEvent.click(screen.getByTestId("cab-member-add"));
    fireEvent.click(screen.getByTestId("cab-member-kind-0"));
    expect(screen.getByTestId("cab-member-kind-0-menu-option-customer_user")).toBeInTheDocument();
  });

  it("explains refused saves in both error shapes", async () => {
    let shape: "code" | "member" | "other" | "list" = "list";
    serveChangeAdmin([], (method, path) => {
      if (method !== "PUT" || path !== `${BASE}/2`) return undefined;
      if (shape === "code") throw new ApiError(422, { detail: { code: "cab_invalid", reason: "quorum_exceeds_members", message: "step 'CAB': quorum 5 exceeds distinct approvers" } }, path);
      if (shape === "member") throw new ApiError(422, { detail: { code: "cab_invalid", reason: "invalid_member" } }, path);
      if (shape === "other") throw new ApiError(422, { detail: { code: "cab_invalid", message: "duplicate step names: CAB" } }, path);
      throw new ApiError(422, { detail: [{ loc: ["body", "quorum"], msg: "Input should be less than or equal to 100", type: "less_than_equal" }] }, path);
    });
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(within(await rowOf(2)).getByTestId("cab-edit"));
    fireEvent.click(await screen.findByTestId("cab-save"));
    expect(await screen.findByTestId("cab-error")).toHaveTextContent("Die Eingabe ist ungültig (Feld: quorum).");
    shape = "code";
    fireEvent.click(screen.getByTestId("cab-save"));
    await waitFor(() => expect(screen.getByTestId("cab-error")).toHaveTextContent("Das Quorum ist größer als die Zahl der Mitglieder."));
    shape = "member";
    fireEvent.click(screen.getByTestId("cab-save"));
    await waitFor(() => expect(screen.getByTestId("cab-error")).toHaveTextContent("Ein Mitglied ist ungültig oder existiert nicht mehr."));
    shape = "other";
    fireEvent.click(screen.getByTestId("cab-save"));
    await waitFor(() => expect(screen.getByTestId("cab-error")).toHaveTextContent("Entscheidungsregel, Mindestzahl und Mitglieder passen nicht zusammen."));
  });

  it("says why a board with meetings cannot be deleted", async () => {
    serveChangeAdmin([], (method, path) => {
      if (method === "DELETE" && path === `${BASE}/1`) throw new ApiError(409, { detail: { code: "cab_has_meetings", meetings: 2 } }, path);
      return undefined;
    });
    renderWithClient(<ChangeCabTab />);
    fireEvent.click(within(await rowOf(1)).getByTestId("cab-delete"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect(await screen.findByTestId("cab-delete-error")).toHaveTextContent("Das CAB hat CAB-Sitzungen und kann nicht gelöscht werden.");
  });
});
