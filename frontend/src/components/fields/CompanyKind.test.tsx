import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { useState } from "react";
import i18n from "@/i18n";
import { FieldInput } from "./FieldInput";
import type { FieldDef, FieldValue } from "./types";

const { companies } = vi.hoisted(() => ({ companies: vi.fn() }));
vi.mock("@/lib/cmdbApi", async (orig) => {
  const mod = await orig<typeof import("@/lib/cmdbApi")>();
  return { ...mod, cmdbApi: { ...mod.cmdbApi, companies } };
});

const def: FieldDef = { key: "Owner", kind: "company", label: "Firma" };

function Harness({ onValue }: { onValue?: (v: FieldValue) => void }) {
  const [value, setValue] = useState<FieldValue>("");
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <FieldInput
          def={def}
          value={value}
          testId="co"
          onChange={(v) => {
            setValue(v);
            onValue?.(v);
          }}
        />
      </I18nextProvider>
    </QueryClientProvider>
  );
}

describe("company field kind", () => {
  beforeEach(() => {
    companies.mockReset();
    companies.mockResolvedValue([{ id: "example-corp", label: "Example Corp" }]);
  });

  it("searches companies, picks the customer id and clears it again", async () => {
    const onValue = vi.fn();
    render(<Harness onValue={onValue} />);
    fireEvent.change(screen.getByTestId("co"), { target: { value: "exa" } });
    const hit = await screen.findByTestId("co-result-example-corp");
    expect(companies).toHaveBeenCalledWith("exa");
    fireEvent.mouseDown(hit);
    expect(onValue).toHaveBeenLastCalledWith("example-corp");
    await waitFor(() => expect((screen.getByTestId("co") as HTMLInputElement).value).toBe("example-corp"));
    fireEvent.click(screen.getByTestId("co-clear"));
    expect(onValue).toHaveBeenLastCalledWith(null);
    expect((screen.getByTestId("co") as HTMLInputElement).value).toBe("");
  });

  it("does not turn typed text into a value", () => {
    const onValue = vi.fn();
    render(<Harness onValue={onValue} />);
    fireEvent.change(screen.getByTestId("co"), { target: { value: "exa" } });
    expect(onValue).not.toHaveBeenCalled();
  });
});
