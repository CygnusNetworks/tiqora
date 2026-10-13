import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { useState } from "react";
import i18n from "@/i18n";
import type { AttributeDef, CmdbAttachment } from "@/lib/cmdbApi";
import { emptyValues, type AttrValues } from "./attributeModel";
import { CiAttributeForm } from "./CiAttributeForm";

const mk = (over: Partial<AttributeDef>): AttributeDef =>
  ({ key: "X", name: "X", type: "text", count_min: 0, count_max: 1, count_default: 1, required: false, searchable: false, portal: false, default: null, help: null, options: {}, sub: [], ...over }) as AttributeDef;

const IP = mk({ key: "IPAddress", name: "IP-Adresse", type: "ip", count_max: 4 });
const NIC = mk({ key: "NIC", name: "Netzwerkkarte", count_max: 2, sub: [IP] });
const SPECS = mk({ key: "Specs", name: "Technik", type: "group", sub: [mk({ key: "CPU", name: "CPU", type: "integer" })] });
const DOC = mk({ key: "Doc", name: "Dokument", type: "attachment" });

function Harness({
  defs,
  errors,
  attachments,
  onUpload,
  seen,
}: {
  defs: AttributeDef[];
  errors?: Record<string, string>;
  attachments?: CmdbAttachment[];
  onUpload?: (f: File) => Promise<CmdbAttachment | void>;
  seen?: (v: AttrValues) => void;
}) {
  const [values, setValues] = useState<AttrValues>(emptyValues(defs));
  const qc = new QueryClient();
  return (
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CiAttributeForm
          defs={defs}
          values={values}
          options={{}}
          errors={errors}
          attachments={attachments}
          onUpload={onUpload}
          onChange={(v) => {
            setValues(v);
            seen?.(v);
          }}
        />
      </I18nextProvider>
    </QueryClientProvider>
  );
}

describe("CiAttributeForm", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });

  it("disables the add button at count_max and the remove button at count_min", () => {
    render(<Harness defs={[NIC]} />);
    const add = screen.getByTestId("ci-attr-NIC-add");
    expect(add).toBeEnabled();
    fireEvent.click(add);
    expect(screen.getByTestId("ci-attr-NIC-2")).toBeInTheDocument();
    expect(screen.getByTestId("ci-attr-NIC-add")).toBeDisabled();
    fireEvent.click(screen.getByTestId("ci-attr-NIC-2-remove"));
    expect(screen.queryByTestId("ci-attr-NIC-2")).toBeNull();
    expect(screen.getByTestId("ci-attr-NIC-add")).toBeEnabled();
  });

  it("does not allow removing below count_min", () => {
    render(<Harness defs={[mk({ key: "Disk", name: "Platte", count_min: 1, count_max: 3 })]} />);
    expect(screen.getByTestId("ci-attr-Disk-1-remove")).toBeDisabled();
  });

  it("announces a backend path error on the nested input via aria-describedby", () => {
    render(<Harness defs={[NIC]} errors={{ "NIC[1].IPAddress[1]": "cmdb.validation.bad_format" }} />);
    const input = screen.getByTestId("ci-attr-NIC-1-IPAddress-1-input");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const errorId = input.getAttribute("aria-describedby") as string;
    expect(document.getElementById(errorId)).toHaveTextContent("Der Wert hat ein ungültiges Format.");
  });

  it("nests sub attributes under their entry", () => {
    render(<Harness defs={[NIC]} />);
    expect(screen.getByTestId("ci-attr-NIC-1")).toContainElement(screen.getByTestId("ci-attr-NIC-1-IPAddress-1-input"));
  });

  it("renders a group as a fieldset with a legend and edits the values inside", () => {
    const seen = vi.fn();
    render(<Harness defs={[SPECS]} seen={seen} />);
    const fs = screen.getByRole("group", { name: "Technik" });
    expect(fs.tagName).toBe("FIELDSET");
    fireEvent.change(screen.getByTestId("ci-attr-Specs-1-CPU-1-input"), { target: { value: "8" } });
    expect(seen).toHaveBeenLastCalledWith({ Specs: [{ v: null, s: { CPU: [{ v: 8 }] } }] });
  });

  it("marks required attributes and shows help text", () => {
    render(<Harness defs={[mk({ key: "Vendor", name: "Hersteller", required: true, help: "Name laut Rechnung" })]} />);
    expect(screen.getByText("Name laut Rechnung")).toBeInTheDocument();
    expect(screen.getByText("(Pflichtfeld)", { exact: false })).toBeInTheDocument();
  });

  it("uploads a file for an attachment attribute and selects it", async () => {
    const att = { id: 5, filename: "plan.pdf", content_type: "application/pdf", size_bytes: 3, ci_id: null, created_at: "2026-10-12T10:00:00Z" } as CmdbAttachment;
    const onUpload = vi.fn().mockResolvedValue(att);
    const seen = vi.fn();
    render(<Harness defs={[DOC]} attachments={[att]} onUpload={onUpload} seen={seen} />);
    const file = new File(["abc"], "plan.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByTestId("ci-attr-Doc-1-input-file"), { target: { files: [file] } });
    await waitFor(() => expect(seen).toHaveBeenLastCalledWith({ Doc: [{ v: 5 }] }));
    expect(onUpload).toHaveBeenCalledWith(file);
  });
});
