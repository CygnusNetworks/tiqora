import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { useMemo, useState } from "react";
import i18n from "@/i18n";
import { PrechatTab } from "./PrechatTab";
import { newDraft, validateDraft, type Draft } from "@/lib/webchatForm";

function Harness({ initial }: { initial?: Draft }) {
  const [draft, setDraft] = useState<Draft>(initial ?? newDraft());
  const errors = useMemo(() => validateDraft(draft), [draft]);
  return (
    <I18nextProvider i18n={i18n}>
      <PrechatTab draft={draft} errors={errors} onDraft={setDraft} dynamicFields={[{ value: "Region", label: "Region" }]} />
      <button type="button" disabled={Object.keys(errors).length > 0} data-testid="harness-save">
        save
      </button>
    </I18nextProvider>
  );
}

function pick(testId: string, value: string) {
  fireEvent.click(screen.getByTestId(testId));
  fireEvent.click(screen.getByTestId(`${testId}-menu-option-${value}`));
}

function validDraft(): Draft {
  const base = newDraft();
  base.name = "X";
  base.values.topics = [{ key: "a", label: "A", queue_id: 1, priority_id: null, ai_mode: "off" }];
  return base;
}

describe("PrechatTab", () => {
  beforeAll(() => i18n.changeLanguage("de"));
  afterAll(() => i18n.changeLanguage("en"));

  it("prechat tab: select needs options, preview uses FieldsForm", () => {
    render(<Harness initial={validDraft()} />);
    expect(screen.getByTestId("harness-save")).toBeEnabled();

    fireEvent.click(screen.getByTestId("webchat-field-add"));
    fireEvent.change(screen.getByTestId("webchat-field-2-key"), { target: { value: "product" } });
    fireEvent.change(screen.getByTestId("webchat-field-2-label"), { target: { value: "Produkt" } });
    pick("webchat-field-2-kind", "select");

    expect(screen.getByTestId("webchat-field-2-options-error")).toHaveTextContent(
      "Bitte mindestens einen Auswahlwert angeben.",
    );
    expect(screen.getByTestId("harness-save")).toBeDisabled();

    fireEvent.click(screen.getByTestId("webchat-field-2-option-add"));
    fireEvent.change(screen.getByTestId("webchat-field-2-option-0-value"), { target: { value: "router" } });
    fireEvent.change(screen.getByTestId("webchat-field-2-option-0-label"), { target: { value: "Router" } });
    expect(screen.queryByTestId("webchat-field-2-options-error")).toBeNull();
    expect(screen.getByTestId("harness-save")).toBeEnabled();

    // the wave-0 field engine renders the preview
    expect(screen.getByTestId("webchat-prechat-preview-product")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("webchat-prechat-preview-product"));
    expect(screen.getByTestId("webchat-prechat-preview-product-menu-option-router")).toHaveTextContent("Router");
  });

  it("rejects a second email/name mapping and the reserved keys (R-PRECHAT-KEYS)", () => {
    render(<Harness initial={validDraft()} />);
    fireEvent.click(screen.getByTestId("webchat-field-add"));
    fireEvent.change(screen.getByTestId("webchat-field-2-key"), { target: { value: "topic" } });
    fireEvent.change(screen.getByTestId("webchat-field-2-label"), { target: { value: "Thema" } });
    expect(screen.getByTestId("webchat-field-2-key-error")).toHaveTextContent("reserviert");

    fireEvent.change(screen.getByTestId("webchat-field-2-key"), { target: { value: "mail2" } });
    pick("webchat-field-2-maps", "email");
    expect(screen.getByTestId("webchat-field-2-maps-error")).toHaveTextContent("Nur ein Feld darf in „E-Mail“");
    expect(screen.getByTestId("harness-save")).toBeDisabled();
    pick("webchat-field-2-maps", "none");
    expect(screen.getByTestId("harness-save")).toBeEnabled();
  });
});
