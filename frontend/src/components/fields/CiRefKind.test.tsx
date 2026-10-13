import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { useState } from "react";
import i18n from "@/i18n";
import { FieldInput } from "./FieldInput";
import type { FieldDef, FieldValue } from "./types";

const { lookup, get } = vi.hoisted(() => ({ lookup: vi.fn(), get: vi.fn() }));
vi.mock("@/lib/cmdbApi", async (orig) => {
  const mod = await orig<typeof import("@/lib/cmdbApi")>();
  return { ...mod, cmdbApi: { ...mod.cmdbApi, lookup, get } };
});

const def: FieldDef = { key: "Location", kind: "ci_ref", label: "Standort", lookupClassKeys: ["location"] };

function Harness({ initial = "", onValue }: { initial?: FieldValue; onValue?: (v: FieldValue) => void }) {
  const [value, setValue] = useState<FieldValue>(initial);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <FieldInput
          def={def}
          value={value}
          testId="ci"
          onChange={(v) => {
            setValue(v);
            onValue?.(v);
          }}
        />
      </I18nextProvider>
    </QueryClientProvider>
  );
}

describe("ci_ref field kind", () => {
  beforeEach(() => {
    lookup.mockReset();
    get.mockReset();
    lookup.mockResolvedValue([{ id: 742010, number: "1000042", name: "Web server", class_key: "location", class_name: "Standort" }]);
  });

  it("looks up with the class keys and sets the id string on pick", async () => {
    const onValue = vi.fn();
    render(<Harness onValue={onValue} />);
    fireEvent.change(screen.getByTestId("ci"), { target: { value: "web" } });
    const hit = await screen.findByTestId("ci-result-742010");
    expect(lookup).toHaveBeenCalledWith("web", ["location"]);
    fireEvent.mouseDown(hit);
    expect(onValue).toHaveBeenLastCalledWith("742010");
    await waitFor(() => expect(screen.queryByTestId("ci-results")).toBeNull());
    expect((screen.getByTestId("ci") as HTMLInputElement).value).toBe("1000042 – Web server");
  });

  it("shows number – name for an existing value and clears it", async () => {
    get.mockResolvedValue({ id: 742010, number: "1000042", name: "Web server" });
    const onValue = vi.fn();
    render(<Harness initial="742010" onValue={onValue} />);
    await waitFor(() => expect((screen.getByTestId("ci") as HTMLInputElement).value).toBe("1000042 – Web server"));
    expect(get).toHaveBeenCalledWith(742010);
    fireEvent.click(screen.getByTestId("ci-clear"));
    expect(onValue).toHaveBeenLastCalledWith(null);
  });

  it("falls back to #id when the CI cannot be read", async () => {
    get.mockRejectedValue(new Error("403"));
    render(<Harness initial="7" />);
    await waitFor(() => expect((screen.getByTestId("ci") as HTMLInputElement).value).toBe("#7"));
  });
});
