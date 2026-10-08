import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { DynamicFieldsForm } from "./DynamicFieldsForm";

afterEach(() => setDisplayTimeZone(null));

describe("DynamicFieldsForm", () => {
  it("shows UTC datetimes in the agent zone and emits UTC lists", () => {
    setDisplayTimeZone("Europe/Berlin");
    const onChange = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <DynamicFieldsForm
          testId="df"
          fields={[{ name: "When", label: "Wann", field_type: "DateTime", required: false, possible_values: null }]}
          values={{ When: ["2026-07-01 08:30:00"] }}
          onChange={onChange}
        />
      </I18nextProvider>,
    );
    const input = screen.getByTestId("df-When");
    expect(input).toHaveValue("2026-07-01T10:30");
    fireEvent.change(input, { target: { value: "2026-07-01T12:00" } });
    expect(onChange).toHaveBeenCalledWith("When", ["2026-07-01 10:00:00"]);
  });
});
