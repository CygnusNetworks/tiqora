import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { DynamicFieldConfigEditor, type DynamicFieldConfig } from "./DynamicFieldConfigEditor";

function renderHarness(fieldType: string, initial: DynamicFieldConfig, onChange: (v: DynamicFieldConfig) => void) {
  return render(
    <I18nextProvider i18n={i18n}>
      <DynamicFieldConfigEditor fieldType={fieldType} value={initial} onChange={onChange} />
    </I18nextProvider>,
  );
}

describe("DynamicFieldConfigEditor", () => {
  it("renders nothing for an unknown field type", () => {
    const { container } = render(
      <I18nextProvider i18n={i18n}>
        <DynamicFieldConfigEditor fieldType="Weird" value={{}} onChange={vi.fn()} />
      </I18nextProvider>,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders a default-value text input for Text/TextArea types", () => {
    const onChange = vi.fn();
    renderHarness("Text", {}, onChange);
    expect(screen.getByTestId("dynamic-field-config-text")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("dynamic-field-default-value"), {
      target: { value: "hello" },
    });
    expect(onChange).toHaveBeenCalledWith({ DefaultValue: "hello" });
  });

  it("renders a checkbox default value for the Checkbox type", () => {
    const onChange = vi.fn();
    renderHarness("Checkbox", {}, onChange);
    const checkbox = screen.getByTestId("dynamic-field-default-value");
    expect(screen.getByTestId("dynamic-field-config-checkbox")).toBeInTheDocument();
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);
    expect(onChange).toHaveBeenCalledWith({ DefaultValue: true });
  });

  it("omits DefaultValue from Checkbox config when unchecked", () => {
    const onChange = vi.fn();
    renderHarness("Checkbox", { DefaultValue: true }, onChange);
    fireEvent.click(screen.getByTestId("dynamic-field-default-value"));
    expect(onChange).toHaveBeenCalledWith({});
  });

  it("renders YearsInPast/YearsInFuture number inputs for Date/DateTime types", () => {
    const onChange = vi.fn();
    renderHarness("DateTime", {}, onChange);
    expect(screen.getByTestId("dynamic-field-config-datetime")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("dynamic-field-years-in-past"), {
      target: { value: "5" },
    });
    expect(onChange).toHaveBeenCalledWith({ YearsInPast: 5 });

    fireEvent.change(screen.getByTestId("dynamic-field-years-in-future"), {
      target: { value: "10" },
    });
    expect(onChange).toHaveBeenCalledWith({ YearsInFuture: 10 });
  });

  it("renders PossibleValues rows for Dropdown/Multiselect types and supports add/edit/remove", () => {
    const onChange = vi.fn();
    renderHarness("Dropdown", { PossibleValues: { open: "Open" } }, onChange);
    expect(screen.getByTestId("dynamic-field-config-select")).toBeInTheDocument();
    expect(screen.getByTestId("dynamic-field-option-key-0")).toHaveValue("open");
    expect(screen.getByTestId("dynamic-field-option-label-0")).toHaveValue("Open");

    fireEvent.click(screen.getByTestId("dynamic-field-option-add"));
    expect(onChange).toHaveBeenLastCalledWith({ PossibleValues: { open: "Open", "": "" } });

    fireEvent.change(screen.getByTestId("dynamic-field-option-label-0"), {
      target: { value: "Open Ticket" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ PossibleValues: { open: "Open Ticket" } });

    fireEvent.click(screen.getByTestId("dynamic-field-option-remove-0"));
    expect(onChange).toHaveBeenLastCalledWith({ PossibleValues: {} });
  });

  it("shows a required-options warning when Dropdown has no possible values", () => {
    renderHarness("Multiselect", {}, vi.fn());
    expect(screen.getByText("Add at least one option.")).toBeInTheDocument();
  });

  it("toggles PossibleNone for select types", () => {
    const onChange = vi.fn();
    renderHarness("Dropdown", { PossibleValues: { a: "A" } }, onChange);
    fireEvent.click(screen.getByTestId("dynamic-field-possible-none"));
    expect(onChange).toHaveBeenCalledWith({ PossibleValues: { a: "A" }, PossibleNone: true });
  });

  it("switches sub-forms when fieldType changes across rerenders", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <DynamicFieldConfigEditor fieldType="Text" value={{}} onChange={onChange} />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("dynamic-field-config-text")).toBeInTheDocument();

    rerender(
      <I18nextProvider i18n={i18n}>
        <DynamicFieldConfigEditor fieldType="Checkbox" value={{}} onChange={onChange} />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId("dynamic-field-config-text")).not.toBeInTheDocument();
    expect(screen.getByTestId("dynamic-field-config-checkbox")).toBeInTheDocument();
  });
});
