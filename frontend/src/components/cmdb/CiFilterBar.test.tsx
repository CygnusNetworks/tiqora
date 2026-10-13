import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { CatalogItem, CmdbClass, FilterField } from "@/lib/cmdbApi";
import { CiFilterBar } from "./CiFilterBar";
import { emptyFilterState, type CiFilterState } from "./ciFilterModel";

const CLASSES = [{ id: 1, name: "Server" }, { id: 2, name: "Standort" }] as CmdbClass[];
const DEPLOY = [
  { id: 1, name: "Geplant" },
  { id: 2, name: "Produktiv" },
] as CatalogItem[];
const FIELDS: FilterField[] = [
  { key: "attr.hostname", label: "Hostname", label_key: null, type: "string", ops: ["eq", "contains", "is_empty"], enum_values: [], options_source: null },
  { key: "attr.cpus", label: "CPUs", label_key: null, type: "int", ops: ["eq", "gte"], enum_values: [], options_source: null },
];

function setup(state: CiFilterState, attrFields = FIELDS) {
  const onChange = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <CiFilterBar state={state} onChange={onChange} classes={CLASSES} attrFields={attrFields} deploymentItems={DEPLOY} />
    </I18nextProvider>,
  );
  return onChange;
}

describe("CiFilterBar", () => {
  it("disables the attribute filter without a class and says why", async () => {
    await i18n.changeLanguage("de");
    setup(emptyFilterState());
    expect(screen.getByTestId("ci-attr-add")).toBeDisabled();
    expect(screen.getByTestId("ci-attr-filter")).toHaveTextContent("Wählen Sie eine Klasse");
  });

  it("adds an attribute row for a selected class", () => {
    const onChange = setup({ ...emptyFilterState(), classId: 1 });
    expect(screen.getByTestId("ci-attr-add")).toBeEnabled();
    fireEvent.click(screen.getByTestId("ci-attr-add"));
    expect(onChange).toHaveBeenCalledWith({ ...emptyFilterState(), classId: 1, attr: [{ field: "attr.hostname", op: "eq", value: "" }] });
  });

  it("toggles deployment and incident chips", () => {
    const onChange = setup({ ...emptyFilterState(), deployment: [2] });
    expect(screen.getByTestId("ci-filter-dep-2")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByTestId("ci-filter-dep-1"));
    expect(onChange).toHaveBeenLastCalledWith({ ...emptyFilterState(), deployment: [2, 1] });
    fireEvent.click(screen.getByTestId("ci-filter-dep-2"));
    expect(onChange).toHaveBeenLastCalledWith({ ...emptyFilterState(), deployment: [] });
    fireEvent.click(screen.getByTestId("ci-filter-inc-warning"));
    expect(onChange).toHaveBeenLastCalledWith({ ...emptyFilterState(), deployment: [2], incident: ["warning"] });
  });

  it("types numbers into int attribute rows and hides the value for is_empty", () => {
    const onChange = setup({ ...emptyFilterState(), classId: 1, attr: [{ field: "attr.cpus", op: "gte", value: "" }] });
    fireEvent.change(screen.getByTestId("ci-attr-value-0"), { target: { value: "4" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...emptyFilterState(), classId: 1, attr: [{ field: "attr.cpus", op: "gte", value: 4 }] });
  });

  it("shows no value input for value-less operators and removes rows", () => {
    const onChange = setup({ ...emptyFilterState(), classId: 1, attr: [{ field: "attr.hostname", op: "is_empty", value: undefined }] });
    expect(screen.queryByTestId("ci-attr-value-0")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("ci-attr-remove-0"));
    expect(onChange).toHaveBeenLastCalledWith({ ...emptyFilterState(), classId: 1, attr: [] });
  });

  it("resets the attribute rows when the class changes", () => {
    const onChange = setup({ ...emptyFilterState(), classId: 1, attr: [{ field: "attr.hostname", op: "eq", value: "x" }] });
    fireEvent.click(screen.getByTestId("ci-filter-class"));
    fireEvent.click(screen.getByTestId("ci-filter-class-menu-option-2"));
    expect(onChange).toHaveBeenLastCalledWith({ ...emptyFilterState(), classId: 2, attr: [] });
  });
});
