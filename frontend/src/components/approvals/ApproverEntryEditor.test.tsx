import { beforeAll, describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { ApproverEntryEditor, type ApproverEntry } from "./ApproverEntryEditor";
import { ApiError } from "@/lib/api";
import { mockApi, renderApproval, useGerman } from "./approvalsTestHarness";

beforeAll(useGerman);

let latest: ApproverEntry[] = [];

function Harness({ initial = [], allowCustomers = true }: { initial?: ApproverEntry[]; allowCustomers?: boolean }) {
  const [value, setValue] = useState(initial);
  latest = value;
  return <ApproverEntryEditor value={value} onChange={setValue} subjectType="ticket" allowCustomers={allowCustomers} />;
}

async function pick(testId: string, optionValue: string) {
  fireEvent.click(screen.getByTestId(testId));
  fireEvent.click(await screen.findByTestId(`${testId}-menu-option-${optionValue}`));
}

describe("ApproverEntryEditor", () => {
  it("adds an agent row and removes rows", () => {
    mockApi();
    renderApproval(<Harness initial={[{ kind: "group", ref: "7" }]} />);
    expect(screen.getAllByTestId("entry-row")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("entry-add"));
    expect(screen.getAllByTestId("entry-row")).toHaveLength(2);
    expect(latest).toEqual([
      { kind: "group", ref: "7" },
      { kind: "user", ref: "" },
    ]);
    fireEvent.click(screen.getByTestId("entry-remove-0"));
    expect(latest).toEqual([{ kind: "user", ref: "" }]);
    expect(screen.getAllByTestId("entry-row")).toHaveLength(1);
  });

  it("stores the agent id as the ref", async () => {
    mockApi();
    renderApproval(<Harness initial={[{ kind: "user", ref: "" }]} />);
    expect(screen.getByTestId("entry-ref-0")).toHaveTextContent("Agent wählen");
    await pick("entry-ref-0", "3");
    expect(latest).toEqual([{ kind: "user", ref: "3" }]);
    expect(screen.getByTestId("entry-ref-0")).toHaveTextContent("Erika Muster");
  });

  it("switches the picker with the kind and clears the old ref", async () => {
    mockApi();
    renderApproval(<Harness initial={[{ kind: "user", ref: "3" }]} />);
    await pick("entry-kind-0", "group");
    expect(latest).toEqual([{ kind: "group", ref: "" }]);
    expect(screen.getByTestId("entry-ref-0")).toHaveTextContent("Gruppe wählen");
    await pick("entry-ref-0", "7");
    expect(latest).toEqual([{ kind: "group", ref: "7" }]);

    await pick("entry-kind-0", "list");
    expect(screen.getByTestId("entry-ref-0")).toHaveTextContent("Freigabegruppe wählen");
    await pick("entry-ref-0", "1");
    expect(latest).toEqual([{ kind: "list", ref: "1" }]);
    expect(screen.getByTestId("entry-ref-0")).toHaveTextContent("CAB Netzwerk");

    await pick("entry-kind-0", "role");
    expect(screen.getByTestId("entry-ref-0")).toHaveTextContent("Rolle wählen");
    await pick("entry-ref-0", "4");
    expect(latest).toEqual([{ kind: "role", ref: "4" }]);

    await pick("entry-kind-0", "resolver");
    expect(screen.getByTestId("entry-ref-0")).toHaveTextContent("Regel wählen");
    await pick("entry-ref-0", "ticket.owner");
    expect(latest).toEqual([{ kind: "resolver", ref: "ticket.owner" }]);
    expect(screen.getByTestId("entry-ref-0")).toHaveTextContent("Besitzer des Tickets");
  });

  it("finds a customer by typing and stores the login", async () => {
    mockApi();
    renderApproval(<Harness initial={[{ kind: "customer_user", ref: "" }]} />);
    fireEvent.change(screen.getByTestId("entry-ref-0-search"), { target: { value: "ku" } });
    await waitFor(() => expect(screen.getByTestId("entry-ref-0-search")).toHaveValue("ku"));
    await pick("entry-ref-0", "kunde@example.org");
    expect(latest).toEqual([{ kind: "customer_user", ref: "kunde@example.org" }]);
    expect(screen.getByTestId("entry-ref-0")).toHaveTextContent("Kim Kunde (kunde@example.org)");
  });

  it("falls back to a role number when the role list is not readable", async () => {
    mockApi({
      "GET /api/v1/admin/roles": () => {
        throw new ApiError(403, { detail: { code: "forbidden" } }, "/x");
      },
    });
    renderApproval(<Harness initial={[{ kind: "role", ref: "" }]} />);
    const input = await screen.findByPlaceholderText("Rollennummer");
    fireEvent.change(input, { target: { value: "4x" } });
    expect(latest).toEqual([{ kind: "role", ref: "4" }]);
  });

  it("hides customers when they are not allowed", async () => {
    mockApi();
    renderApproval(<Harness initial={[{ kind: "resolver", ref: "" }]} allowCustomers={false} />);
    fireEvent.click(screen.getByTestId("entry-kind-0"));
    await screen.findByTestId("entry-kind-0-menu-option-group");
    expect(screen.queryByTestId("entry-kind-0-menu-option-customer_user")).toBeNull();
    fireEvent.keyDown(screen.getByTestId("entry-kind-0-menu"), { key: "Escape" });

    fireEvent.click(screen.getByTestId("entry-ref-0"));
    await screen.findByTestId("entry-ref-0-menu-option-ticket.owner");
    expect(screen.queryByTestId("entry-ref-0-menu-option-ticket.customer_user")).toBeNull();
  });

  it("offers customers when they are allowed", async () => {
    mockApi();
    renderApproval(<Harness initial={[{ kind: "resolver", ref: "" }]} allowCustomers />);
    fireEvent.click(screen.getByTestId("entry-kind-0"));
    expect(await screen.findByTestId("entry-kind-0-menu-option-customer_user")).toBeInTheDocument();
  });
});
