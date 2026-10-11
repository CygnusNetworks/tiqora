import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { useState } from "react";
import i18n from "@/i18n";
import { FieldInput } from "./FieldInput";
import type { FieldDef, FieldValue } from "./types";

const { search } = vi.hoisted(() => ({ search: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const mod = await orig<typeof import("@/lib/api")>();
  return { ...mod, api: { ...mod.api, searchReferenceCustomers: search } };
});
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: { can_use_customer_directory: false } }) }));

const def: FieldDef = { key: "CustomerUserID", kind: "customer", label: "Kunde" };
const anna = { login: "anna.wf3@example.org", email: "anna.wf3@example.org", customer_id: "wf3-corp", full_name: "Anna Wf3" };

function Harness({ onValue }: { onValue?: (v: FieldValue) => void }) {
  const [value, setValue] = useState<FieldValue>("");
  return (
    <FieldInput
      def={def}
      value={value}
      testId="cust"
      onChange={(v) => {
        setValue(v);
        onValue?.(v);
      }}
    />
  );
}

function wrap(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("customer field kind", () => {
  beforeEach(() => {
    search.mockReset();
    search.mockResolvedValue([anna]);
  });

  it("customer kind searches /reference/customers and returns the login string", async () => {
    const onValue = vi.fn();
    wrap(<Harness onValue={onValue} />);
    fireEvent.change(screen.getByTestId("cust"), { target: { value: "anna" } });
    const hit = await screen.findByTestId("cust-result-anna.wf3@example.org");
    expect(search).toHaveBeenCalledWith({ q: "anna" }, expect.anything());
    fireEvent.mouseDown(hit);
    expect(onValue).toHaveBeenLastCalledWith("anna.wf3@example.org");
    await waitFor(() => expect(screen.queryByTestId("cust-results")).toBeNull());
    expect((screen.getByTestId("cust") as HTMLInputElement).value).toBe("anna.wf3@example.org");
  });

  it("customer kind is keyboard reachable and opens CustomerPickerDialog", async () => {
    wrap(<Harness />);
    const input = screen.getByTestId("cust");
    input.focus();
    expect(document.activeElement).toBe(input);
    // arrow keys and Enter choose a hit without the mouse
    fireEvent.change(input, { target: { value: "anna" } });
    await screen.findByTestId("cust-result-anna.wf3@example.org");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect((input as HTMLInputElement).value).toBe("anna.wf3@example.org"));
    // the button is a real, focusable button and opens the picker dialog
    const pick = screen.getByTestId("cust-pick");
    expect(pick.tagName).toBe("BUTTON");
    expect(pick).not.toBeDisabled();
    pick.focus();
    expect(document.activeElement).toBe(pick);
    fireEvent.click(pick);
    expect(await screen.findByTestId("customer-picker-dialog")).toBeInTheDocument();
  });

  it("a pick in the dialog fills the field with the login", async () => {
    const onValue = vi.fn();
    wrap(<Harness onValue={onValue} />);
    fireEvent.click(screen.getByTestId("cust-pick"));
    const search_ = await screen.findByTestId("customer-picker-search");
    fireEvent.change(search_, { target: { value: "anna" } });
    fireEvent.click(await screen.findByTestId("customer-picker-result-anna.wf3@example.org"));
    expect(onValue).toHaveBeenLastCalledWith("anna.wf3@example.org");
    await waitFor(() => expect(screen.queryByTestId("customer-picker-dialog")).toBeNull());
  });
});
