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
        values={{ pw: null }}
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

  it("binds labels and exposes required, invalid and described-by on every kind", () => {
    const all: FieldDef[] = [
      { key: "topic", kind: "text", label: "Thema", required: true, help: "Hilfe" },
      { key: "area", kind: "select", label: "Bereich", required: true, options: [{ value: "net", label: "Netz" }] },
      { key: "tags", kind: "multiselect", label: "Themen", required: true, options: [{ value: "a", label: "A" }] },
      { key: "t2", kind: "tags", label: "Schlagworte", required: true },
    ];
    wrap(
      <FieldsForm
        fields={all}
        values={{}}
        onChange={() => {}}
        errors={{ topic: "E1", area: "E2", tags: "E3", t2: "E4" }}
        testId="f"
      />,
    );
    for (const f of all) {
      const err = screen.getByTestId(`f-${f.key}-error`);
      expect(err.id).toBe(`f-${f.key}-error`);
    }
    // text, select trigger, tag input: the visible label names the control
    expect(screen.getByLabelText(/^Thema/)).toBe(screen.getByTestId("f-topic"));
    expect(screen.getByLabelText(/^Bereich/)).toBe(screen.getByTestId("f-area"));
    expect(screen.getByLabelText(/^Schlagworte/)).toBe(screen.getByTestId("f-t2-input"));
    expect(screen.getByRole("group", { name: /^Themen/ })).toBe(screen.getByTestId("f-tags"));
    for (const [testId, key] of [
      ["f-topic", "topic"],
      ["f-area", "area"],
      ["f-tags", "tags"],
      ["f-t2-input", "t2"],
    ]) {
      const el = screen.getByTestId(testId);
      expect(el).toHaveAttribute("aria-describedby", `f-${key}-error`);
      expect(el).toHaveAttribute("aria-invalid", "true");
    }
    for (const testId of ["f-topic", "f-tags", "f-t2-input"]) {
      expect(screen.getByTestId(testId)).toHaveAttribute("aria-required", "true");
    }
  });

  it("describes a control by its help text when there is no error", () => {
    wrap(
      <FieldsForm fields={[{ key: "x", kind: "text", label: "X", help: "Tipp" }]} values={{}} onChange={() => {}} testId="f" />,
    );
    expect(screen.getByTestId("f-x")).toHaveAttribute("aria-describedby", "f-x-help");
    expect(document.getElementById("f-x-help")).toHaveTextContent("Tipp");
    expect(screen.getByTestId("f-x")).not.toHaveAttribute("aria-invalid");
  });

  it("secret: null = stored/untouched, empty string = cleared, text = new secret", () => {
    const onChange = vi.fn();
    const def: FieldDef = { key: "pw", kind: "secret", label: "Passwort", secretSet: true };
    const { rerender } = wrap(<FieldsForm fields={[def]} values={{ pw: null }} onChange={onChange} testId="f" />);
    const ui = (v: string | null) => (
      <I18nextProvider i18n={i18n}>
        <FieldsForm fields={[def]} values={{ pw: v }} onChange={onChange} testId="f" />
      </I18nextProvider>
    );
    expect(screen.getByTestId("f-pw")).toHaveAttribute("placeholder", i18n.t("fields.secretStored"));
    expect(screen.getByTestId("f-pw-clear")).toBeInTheDocument();
    rerender(ui(""));
    expect(screen.getByTestId("f-pw")).toHaveAttribute("placeholder", i18n.t("fields.secretCleared"));
    expect(screen.queryByTestId("f-pw-clear")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("f-pw-keep"));
    expect(onChange).toHaveBeenLastCalledWith("pw", null);
    rerender(ui("neu"));
    expect(screen.getByTestId("f-pw")).toHaveValue("neu");
    expect(screen.queryByTestId("f-pw-clear")).not.toBeInTheDocument();
    expect(screen.queryByTestId("f-pw-keep")).not.toBeInTheDocument();
  });

  it("date and datetime round-trip their value and clear to an empty string", () => {
    const onChange = vi.fn();
    wrap(
      <FieldsForm
        fields={[
          { key: "d", kind: "date", label: "D" },
          { key: "dt", kind: "datetime", label: "DT" },
        ]}
        values={{ d: "2026-10-08", dt: "2026-10-08T14:30" }}
        onChange={onChange}
        testId="f"
      />,
    );
    expect(screen.getByTestId("f-d")).toHaveValue("2026-10-08");
    expect(screen.getByTestId("f-dt")).toHaveValue("2026-10-08T14:30");
    fireEvent.change(screen.getByTestId("f-d"), { target: { value: "2026-11-01" } });
    expect(onChange).toHaveBeenLastCalledWith("d", "2026-11-01");
    fireEvent.change(screen.getByTestId("f-dt"), { target: { value: "2026-11-01T09:05" } });
    expect(onChange).toHaveBeenLastCalledWith("dt", "2026-11-01T09:05");
    fireEvent.change(screen.getByTestId("f-d"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith("d", "");
    fireEvent.change(screen.getByTestId("f-dt"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith("dt", "");
  });

  it("tags suggestions use option values", () => {
    wrap(
      <FieldsForm
        fields={[{ key: "t", kind: "tags", label: "T", options: [{ value: "vpn", label: "VPN-Zugang" }] }]}
        values={{ t: [] }}
        onChange={() => {}}
        testId="f"
      />,
    );
    fireEvent.focus(screen.getByTestId("f-t-input"));
    expect(screen.getByTestId("f-t-option-vpn")).toBeInTheDocument();
  });

  it("secret: typing then erasing reports null, only Entfernen reports an empty string", () => {
    const onChange = vi.fn();
    wrap(
      <FieldsForm
        fields={[{ key: "pw", kind: "secret", label: "Passwort", secretSet: true }]}
        values={{ pw: "abc" }}
        onChange={onChange}
        testId="f"
      />,
    );
    fireEvent.change(screen.getByTestId("f-pw"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith("pw", null);
    expect(onChange).not.toHaveBeenCalledWith("pw", "");
  });
});
