import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SchemaForm } from "./SchemaForm";
import type { JsonSchema } from "./jsonSchema";

const schema: JsonSchema = {
  type: "object",
  properties: {
    limit: { type: "integer", minimum: 1, maximum: 10, default: 5, title: "Limit" },
    blob: { type: "object", title: "Blob" },
  },
};

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("SchemaForm", () => {
  it("reports changes with the whole next value set and the changed key", () => {
    const onChange = vi.fn();
    const values = { limit: 5 };
    wrap(<SchemaForm schema={schema} values={values} onChange={onChange} />);
    fireEvent.change(screen.getByTestId("schema-form-limit"), { target: { value: "6" } });
    expect(onChange).toHaveBeenCalledWith({ ...values, limit: 6 }, "limit");
  });

  it("shows unsupported properties instead of dropping them", () => {
    wrap(<SchemaForm schema={schema} values={{ limit: 5 }} onChange={() => {}} />);
    expect(screen.getByTestId("schema-form-unsupported-blob")).toHaveTextContent(
      "This field cannot be edited here.",
    );
  });

  it("shows field errors and honours testId", () => {
    wrap(
      <SchemaForm schema={schema} values={{ limit: 5 }} onChange={() => {}} errors={{ limit: "zu groß" }} testId="x" />,
    );
    expect(screen.getByTestId("x-limit-error")).toHaveTextContent("zu groß");
  });
});
