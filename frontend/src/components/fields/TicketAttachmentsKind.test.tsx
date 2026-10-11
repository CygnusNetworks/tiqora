import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { FieldInput } from "./FieldInput";
import type { FieldDef } from "./types";

const def: FieldDef = { key: "Attachments", kind: "ticket_attachments", label: "Anhänge aus dem Ticket" };
const options = [
  { value: "Article::1::1", label: "#1: a.pdf" },
  { value: "Article::1::2", label: "#1: b.pdf" },
];

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("ticket_attachments field kind", () => {
  it("ticket_attachments renders options as checkboxes and returns keys", () => {
    const onChange = vi.fn();
    wrap(<FieldInput def={def} value={["Article::1::2"]} onChange={onChange} testId="att" options={options} />);
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([false, true]);
    fireEvent.click(boxes[0]);
    expect(onChange).toHaveBeenLastCalledWith(["Article::1::2", "Article::1::1"]);
    fireEvent.click(boxes[1]);
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("says so when the ticket has no attachments", () => {
    wrap(<FieldInput def={def} value={null} onChange={() => {}} testId="att" options={[]} />);
    expect(screen.getByTestId("att")).toHaveTextContent(i18n.t("process.form.attachmentsNone"));
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
