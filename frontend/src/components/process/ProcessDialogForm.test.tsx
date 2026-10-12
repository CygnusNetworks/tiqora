import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { ProcessDialogFormData } from "@/lib/processApi";
import { ProcessDialogForm } from "./ProcessDialogForm";

const base = {
  description_short: "",
  description_long: "",
  default: null,
  options: [],
  time_units: "0" as const,
  uploads: false,
};
const form: ProcessDialogFormData = {
  process_entity_id: "P",
  activity_dialog_entity_id: "AD",
  name: "Bestellung",
  description_short: "Bitte ausfüllen",
  description_long: "",
  submit_button_text: "",
  submit_advice_text: "",
  fields: [
    { ...base, name: "Title", kind: "text", label: "Title", required: true },
    { ...base, name: "DynamicField_Memo", kind: "text", label: "Memo", required: false },
    { ...base, name: "Attachments", kind: "unsupported", label: "Attachments", required: false },
  ],
};

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("ProcessDialogForm", () => {
  it("shows the lead text, the required marker and the unsupported note", () => {
    wrap(<ProcessDialogForm form={form} values={{}} onChange={() => {}} />);
    expect(screen.getByText("Bitte ausfüllen")).toBeInTheDocument();
    expect(screen.getByTestId("process-form-Title-label").textContent).toContain("*");
    expect(screen.getByTestId("process-form-DynamicField_Memo-label").textContent).not.toContain("*");
    expect(screen.getByText(i18n.t("process.form.unsupported"))).toBeInTheDocument();
  });

  it("reports typed values by field key", () => {
    const onChange = vi.fn();
    wrap(<ProcessDialogForm form={form} values={{ Title: "" }} onChange={onChange} />);
    fireEvent.change(screen.getByTestId("process-form-Title"), { target: { value: "Neu" } });
    expect(onChange).toHaveBeenCalledWith("Title", "Neu");
  });

  it("shows field errors", () => {
    wrap(<ProcessDialogForm form={form} values={{}} onChange={() => {}} errors={{ Title: "Pflichtfeld" }} />);
    expect(screen.getByTestId("process-form-Title-error")).toHaveTextContent("Pflichtfeld");
  });
  const mixed: ProcessDialogFormData = {
    ...form,
    fields: [
      { ...base, name: "CustomerUserID", kind: "customer", label: "Customer", required: false },
      { ...base, name: "Article", kind: "article", label: "Article", required: false, time_units: "1", uploads: true },
      {
        ...base,
        name: "Attachments",
        kind: "ticket_attachments",
        label: "Attachments",
        required: false,
        options: [{ value: "Article::1::1", label: "#1: wf3.pdf" }],
      },
    ],
  };

  it("portal form never renders customer or ticket_attachments kinds", () => {
    wrap(<ProcessDialogForm form={mixed} values={{}} onChange={() => {}} scope="portal" />);
    expect(screen.queryByTestId("process-form-CustomerUserID")).toBeNull();
    expect(screen.queryByTestId("process-form-Attachments")).toBeNull();
    expect(screen.getByTestId("process-form-Article.Subject")).toBeInTheDocument();
  });

  it("agent form shows the ticket attachments, the time field and the upload area under the article text", () => {
    const onChange = vi.fn();
    wrap(
      <ProcessDialogForm
        form={{ ...mixed, fields: mixed.fields.slice(1) }}
        values={{ Attachments: [] }}
        onChange={onChange}
        uploads={<div data-testid="slot-uploads" />}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith("Attachments", ["Article::1::1"]);
    const body = screen.getByTestId("process-form-Article.Body-label").parentElement!;
    expect(body).toContainElement(screen.getByTestId("slot-uploads"));
    expect(screen.getByTestId("process-form-TimeUnits")).toBeInTheDocument();
  });
});
