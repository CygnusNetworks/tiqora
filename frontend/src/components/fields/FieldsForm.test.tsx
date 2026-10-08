import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { FieldsForm } from "./FieldsForm";
import type { FieldDef } from "./types";

const fields: FieldDef[] = [
  { key: "topic", kind: "text", label: "Thema", required: true },
  {
    key: "area",
    kind: "select",
    label: "Bereich",
    allowEmpty: true,
    options: [
      { value: "net", label: "Netz" },
      { value: "tv", label: "TV" },
    ],
  },
  { key: "urgent", kind: "boolean", label: "Dringend" },
  {
    key: "tags",
    kind: "multiselect",
    label: "Themen",
    options: [
      { value: "a", label: "A" },
      { value: "b", label: "B" },
    ],
  },
];

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("FieldsForm", () => {
  it("renders every kind and reports typed values", () => {
    const onChange = vi.fn();
    wrap(
      <FieldsForm
        fields={fields}
        values={{ topic: "", area: null, urgent: false, tags: ["a"] }}
        onChange={onChange}
        testId="f"
      />,
    );
    fireEvent.change(screen.getByTestId("f-topic"), { target: { value: "VPN" } });
    expect(onChange).toHaveBeenLastCalledWith("topic", "VPN");
    fireEvent.click(screen.getByTestId("f-area"));
    fireEvent.click(screen.getByTestId("f-area-menu-option-tv"));
    expect(onChange).toHaveBeenLastCalledWith("area", "tv");
    fireEvent.click(screen.getByTestId("f-urgent"));
    expect(onChange).toHaveBeenLastCalledWith("urgent", true);
    fireEvent.click(screen.getByTestId("f-tags-remove-a"));
    expect(onChange).toHaveBeenLastCalledWith("tags", []);
  });

  it("shows errors and the required marker", () => {
    wrap(<FieldsForm fields={fields} values={{}} onChange={() => {}} errors={{ topic: "Pflichtfeld" }} testId="f" />);
    expect(screen.getByTestId("f-topic-error")).toHaveTextContent("Pflichtfeld");
    expect(screen.getByTestId("f-topic")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByTestId("f-topic-label")).toHaveTextContent("*");
    expect(screen.getByTestId("f-area-label")).not.toHaveTextContent("*");
  });

  it("options from optionsByKey win over static options", () => {
    wrap(
      <FieldsForm
        fields={[{ ...fields[1], optionsKey: "area" }]}
        values={{ area: null }}
        onChange={() => {}}
        optionsByKey={{ area: [{ value: "x", label: "X" }] }}
        testId="f"
      />,
    );
    fireEvent.click(screen.getByTestId("f-area"));
    expect(screen.getByTestId("f-area-menu-option-x")).toBeInTheDocument();
    expect(screen.queryByTestId("f-area-menu-option-net")).not.toBeInTheDocument();
  });

  it("multiselect add menu lists only unselected options and adds one", () => {
    const onChange = vi.fn();
    wrap(<FieldsForm fields={[fields[3]]} values={{ tags: ["a"] }} onChange={onChange} testId="f" />);
    fireEvent.click(screen.getByTestId("f-tags-add"));
    expect(screen.queryByTestId("f-tags-add-menu-option-a")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("f-tags-add-menu-option-b"));
    expect(onChange).toHaveBeenLastCalledWith("tags", ["a", "b"]);
  });

  it("secret with secretSet shows stored hint and clear sets empty string", () => {
    const onChange = vi.fn();
    wrap(
      <FieldsForm
        fields={[{ key: "pw", kind: "secret", label: "Passwort", secretSet: true }]}
        values={{ pw: "" }}
        onChange={onChange}
        testId="f"
      />,
    );
    expect(screen.getByTestId("f-pw")).toHaveAttribute("type", "password");
    expect(screen.getByTestId("f-pw")).toHaveAttribute("placeholder", i18n.t("fields.secretStored"));
    fireEvent.click(screen.getByTestId("f-pw-clear"));
    expect(onChange).toHaveBeenLastCalledWith("pw", "");
  });

  it("number reports numbers and null when cleared; textarea and date are wired", () => {
    const onChange = vi.fn();
    wrap(
      <FieldsForm
        fields={[
          { key: "n", kind: "number", label: "N" },
          { key: "d", kind: "date", label: "D" },
          { key: "dt", kind: "datetime", label: "DT" },
          { key: "ta", kind: "textarea", label: "TA" },
        ]}
        values={{ n: 3, d: "", dt: "", ta: "" }}
        onChange={onChange}
        testId="f"
      />,
    );
    fireEvent.change(screen.getByTestId("f-n"), { target: { value: "5" } });
    expect(onChange).toHaveBeenLastCalledWith("n", 5);
    fireEvent.change(screen.getByTestId("f-n"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith("n", null);
    expect(screen.getByTestId("f-d")).toHaveAttribute("type", "date");
    expect(screen.getByTestId("f-dt")).toHaveAttribute("type", "datetime-local");
    expect(screen.getByTestId("f-ta").tagName).toBe("TEXTAREA");
    fireEvent.change(screen.getByTestId("f-ta"), { target: { value: "hi" } });
    expect(onChange).toHaveBeenLastCalledWith("ta", "hi");
  });

  it("tags kind renders TagInput and adds a tag", () => {
    const onChange = vi.fn();
    wrap(
      <FieldsForm fields={[{ key: "t", kind: "tags", label: "T" }]} values={{ t: ["x"] }} onChange={onChange} testId="f" />,
    );
    const input = screen.getByTestId("f-t-input");
    fireEvent.change(input, { target: { value: "neu" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith("t", ["x", "neu"]);
  });

  it("unknown kind renders a hint instead of crashing", () => {
    wrap(
      <FieldsForm fields={[{ key: "z", kind: "nope" as never, label: "Z" }]} values={{}} onChange={() => {}} testId="f" />,
    );
    expect(screen.getByTestId("f-z")).toHaveTextContent(i18n.t("fields.unsupported"));
  });

  it("returns nothing for an empty field list", () => {
    const { container } = wrap(<FieldsForm fields={[]} values={{}} onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
