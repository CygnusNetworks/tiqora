import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { insertTagAtCursor, OTRS_PLACEHOLDERS } from "./otrsPlaceholders";
import { VariableReference } from "./VariableReference";

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("VariableReference", () => {
  it("renders categorised groups when expanded", () => {
    wrap(<VariableReference onInsert={vi.fn()} defaultOpen />);

    expect(screen.getByTestId("variable-reference-group-ticket")).toBeInTheDocument();
    expect(screen.getByTestId("variable-reference-group-customer")).toBeInTheDocument();
    expect(screen.getByTestId("variable-reference-group-agent")).toBeInTheDocument();
    expect(screen.getByTestId("variable-reference-group-queue")).toBeInTheDocument();

    // Sample tags from each group are visible
    expect(screen.getByText("<OTRS_TICKET_TicketNumber>")).toBeInTheDocument();
    expect(screen.getByText("<OTRS_CUSTOMER_DATA_wpnum>")).toBeInTheDocument();
    expect(screen.getByText("<OTRS_CURRENT_UserFirstname>")).toBeInTheDocument();
    expect(screen.getByText("<OTRS_QUEUE_Name>")).toBeInTheDocument();
  });

  it("starts collapsed and expands on toggle", () => {
    wrap(<VariableReference onInsert={vi.fn()} />);

    expect(screen.queryByTestId("variable-reference-panel")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("variable-reference-toggle"));
    expect(screen.getByTestId("variable-reference-panel")).toBeInTheDocument();
  });

  it("calls onInsert with the tag when a variable is clicked", () => {
    const onInsert = vi.fn();
    wrap(<VariableReference onInsert={onInsert} defaultOpen />);

    const tag = "<OTRS_TICKET_TicketNumber>";
    const button = screen.getByText(tag).closest("button");
    expect(button).toBeTruthy();
    fireEvent.click(button!);
    expect(onInsert).toHaveBeenCalledTimes(1);
    expect(onInsert).toHaveBeenCalledWith(tag);
  });

  it("exposes a maintainable placeholder catalogue with groups", () => {
    const groups = new Set(OTRS_PLACEHOLDERS.map((p) => p.group));
    expect(groups).toEqual(new Set(["ticket", "customer", "agent", "queue"]));
    expect(OTRS_PLACEHOLDERS.every((p) => p.tag.startsWith("<OTRS_") && p.descriptionKey)).toBe(
      true,
    );
  });
});

describe("insertTagAtCursor", () => {
  it("inserts at selectionStart/selectionEnd", () => {
    const ta = document.createElement("textarea");
    ta.value = "Hello world";
    document.body.appendChild(ta);
    ta.setSelectionRange(6, 6); // before "world"
    const onChange = vi.fn();

    insertTagAtCursor(ta, "Hello world", "<TAG>", onChange);

    expect(onChange).toHaveBeenCalledWith("Hello <TAG>world");
    document.body.removeChild(ta);
  });

  it("appends when control is null", () => {
    const onChange = vi.fn();
    insertTagAtCursor(null, "base", "<TAG>", onChange);
    expect(onChange).toHaveBeenCalledWith("base<TAG>");
  });
});
