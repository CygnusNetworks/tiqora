import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { DelegationCard } from "./DelegationCard";
import { mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";

beforeAll(useGerman);

const DELEGATION = {
  id: 1,
  user_id: 1,
  user_label: "Alex Turner",
  substitute_user_id: 3,
  substitute_label: "Erika Muster",
  valid_from: "2026-10-20T00:00:00Z",
  valid_until: "2030-11-03T00:00:00Z",
  created_at: "2026-10-12T08:00:00Z",
};

const LIST = "GET /api/v1/approvals/delegations";

async function pickSubstitute() {
  fireEvent.click(await screen.findByTestId("delegation-substitute-select"));
  fireEvent.click(await screen.findByTestId("delegation-substitute-select-menu-option-3"));
}

describe("DelegationCard", () => {
  it("lists the current delegation with its period", async () => {
    mockApi({ [LIST]: () => [DELEGATION] });
    renderApproval(<DelegationCard />);
    expect(await screen.findByTestId("delegation-substitute")).toHaveTextContent("Erika Muster");
    expect(screen.getByTestId("delegation-period")).toHaveTextContent("aktiv");
  });

  it("shows the empty text", async () => {
    mockApi({ [LIST]: () => [] });
    renderApproval(<DelegationCard />);
    await waitFor(() =>
      expect(screen.getByTestId("delegation-empty")).toHaveTextContent("Sie haben keine Vertretung benannt."),
    );
  });

  it("creates a delegation with substitute and until, then reloads", async () => {
    const create = vi.fn(() => DELEGATION);
    const list = vi.fn(() => []);
    mockApi({ [LIST]: list, "POST /api/v1/approvals/delegations": create });
    renderApproval(<DelegationCard />);
    await pickSubstitute();
    fireEvent.change(screen.getByTestId("delegation-until"), { target: { value: "2030-11-03T10:00" } });
    fireEvent.click(screen.getByTestId("delegation-create"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const body = (create.mock.calls[0] as unknown as [{ body: Record<string, unknown> }])[0].body;
    expect(body.substitute_user_id).toBe(3);
    expect(body.valid_from).toBeNull();
    expect(body.valid_until).toBe(new Date("2030-11-03T10:00").toISOString());
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("refuses an end date in the past without calling the API", async () => {
    const create = vi.fn();
    mockApi({ [LIST]: () => [], "POST /api/v1/approvals/delegations": create });
    renderApproval(<DelegationCard />);
    await pickSubstitute();
    fireEvent.change(screen.getByTestId("delegation-until"), { target: { value: "2020-01-01T10:00" } });
    fireEvent.click(screen.getByTestId("delegation-create"));
    expect(screen.getByTestId("delegation-period-error")).toHaveTextContent("Das Ende muss in der Zukunft liegen.");
    expect(create).not.toHaveBeenCalled();
  });

  it("asks for a substitute", async () => {
    mockApi({ [LIST]: () => [] });
    renderApproval(<DelegationCard />);
    fireEvent.click(await screen.findByTestId("delegation-create"));
    expect(screen.getByTestId("delegation-substitute-error")).toHaveTextContent("Bitte wählen Sie eine Vertretung.");
  });

  it("explains invalid_member", async () => {
    const err = new ApiError(422, { detail: { code: "invalid_member", detail: null } }, "/x");
    mockApi({
      [LIST]: () => [],
      "POST /api/v1/approvals/delegations": () => {
        throw err;
      },
    });
    renderApproval(<DelegationCard />);
    await pickSubstitute();
    fireEvent.click(screen.getByTestId("delegation-create"));
    expect(await screen.findByTestId("delegation-create-error")).toHaveTextContent(
      "Die gewählte Person ist nicht gültig oder nicht aktiv.",
    );
  });

  it("revokes a delegation and reloads the list", async () => {
    const del = vi.fn(() => undefined);
    const list = vi.fn(() => [DELEGATION]);
    mockApi({ [LIST]: list, "DELETE /api/v1/approvals/delegations/1": del });
    renderApproval(<DelegationCard />);
    fireEvent.click(await screen.findByTestId("delegation-revoke"));
    await waitFor(() => expect(del).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("says 'Bereits beendet' and refreshes when the delegation had already ended", async () => {
    const list = vi.fn(() => [DELEGATION]);
    mockApi({
      [LIST]: list,
      "DELETE /api/v1/approvals/delegations/1": () => {
        throw new ApiError(409, { detail: { code: "delegation_not_active", detail: null } }, "/x");
      },
    });
    renderApproval(<DelegationCard />);
    fireEvent.click(await screen.findByTestId("delegation-revoke"));
    expect(await screen.findByTestId("delegation-ended")).toHaveTextContent("Bereits beendet.");
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});
