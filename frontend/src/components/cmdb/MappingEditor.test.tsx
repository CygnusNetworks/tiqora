import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { MappingEditor } from "./MappingEditor";

const OPTIONS = [
  { value: "attr:hostname", label: "Hostname" },
  { value: "attr:ip", label: "IP-Adresse 1" },
];

function renderEditor(rows: { column: string; target: string }[], samples: string[][] | null = [["a"], []], onChange = vi.fn()) {
  render(
    <I18nextProvider i18n={i18n}>
      <MappingEditor rows={rows} samples={samples} options={OPTIONS} onChange={onChange} />
    </I18nextProvider>,
  );
  return onChange;
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
});

describe("MappingEditor", () => {
  it("shows the column, its samples and the chosen target", () => {
    renderEditor([
      { column: "Rechner", target: "attr:hostname" },
      { column: "Notiz", target: "" },
    ]);
    expect(screen.getByTestId("mapping-row-0")).toHaveTextContent("Rechner");
    expect(screen.getByTestId("mapping-samples-0")).toHaveTextContent("a");
    expect(screen.getByTestId("mapping-target-0")).toHaveTextContent("Hostname");
    expect(screen.getByTestId("mapping-target-1")).toHaveTextContent("Nicht importieren");
  });

  it("reports a changed target for the right row only", () => {
    const onChange = renderEditor([
      { column: "Rechner", target: "" },
      { column: "Notiz", target: "" },
    ]);
    fireEvent.click(screen.getByTestId("mapping-target-1"));
    fireEvent.click(screen.getByTestId("mapping-target-1-menu-option-attr:ip"));
    expect(onChange).toHaveBeenCalledWith([
      { column: "Rechner", target: "" },
      { column: "Notiz", target: "attr:ip" },
    ]);
  });

  it("disables a target another column uses and treats [1] as the same target", () => {
    renderEditor([
      { column: "A", target: "attr:ip" },
      { column: "B", target: "" },
    ]);
    fireEvent.click(screen.getByTestId("mapping-target-1"));
    expect(screen.getByTestId("mapping-target-1-menu-option-attr:ip")).toBeDisabled();
    expect(screen.getByTestId("mapping-target-1-menu-option-attr:hostname")).toBeEnabled();
  });

  it("shows no sample values when the server supplies them (Excel)", () => {
    renderEditor([{ column: "A", target: "" }], null);
    expect(screen.getByTestId("mapping-samples-0")).toHaveTextContent("–");
  });
});
