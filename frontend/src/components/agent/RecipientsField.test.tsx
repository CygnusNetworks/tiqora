import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import {
  RecipientsField,
  parseRecipient,
  parseRecipientList,
  joinRecipients,
  type Recipient,
} from "./RecipientsField";

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("parseRecipient", () => {
  it("parses 'Name <email>' into name + email", () => {
    expect(parseRecipient("Jane Doe <jane@example.com>")).toEqual({
      name: "Jane Doe",
      email: "jane@example.com",
    });
  });

  it("strips quotes around the display name", () => {
    expect(parseRecipient('"Doe, Jane" <jane@example.com>')).toEqual({
      name: "Doe, Jane",
      email: "jane@example.com",
    });
  });

  it("parses a bare email with an empty name", () => {
    expect(parseRecipient("bob@example.com")).toEqual({ name: "", email: "bob@example.com" });
  });

  it("rejects input without an address", () => {
    expect(parseRecipient("not an email")).toBeNull();
    expect(parseRecipient("")).toBeNull();
  });
});

describe("parseRecipientList / joinRecipients", () => {
  it("splits a comma-joined header, ignoring commas inside <>", () => {
    const list = parseRecipientList('"Doe, Jane" <jane@x.com>, bob@x.com');
    expect(list).toEqual([
      { name: "Doe, Jane", email: "jane@x.com" },
      { name: "", email: "bob@x.com" },
    ]);
  });

  it("round-trips through joinRecipients", () => {
    const list: Recipient[] = [
      { name: "Jane", email: "jane@x.com" },
      { name: "", email: "bob@x.com" },
    ];
    expect(joinRecipients(list)).toBe("Jane <jane@x.com>, bob@x.com");
    expect(joinRecipients([])).toBeNull();
  });
});

describe("RecipientsField", () => {
  it("adds a typed recipient on Enter", () => {
    const onChange = vi.fn();
    wrap(
      <RecipientsField
        label="To"
        fieldKey="to"
        recipients={[]}
        onChange={onChange}
        testid="to"
      />,
    );
    const input = screen.getByTestId("to-input");
    fireEvent.change(input, { target: { value: "Jane <jane@x.com>" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith([{ name: "Jane", email: "jane@x.com" }]);
  });

  it("removes a recipient via the × button", () => {
    const onChange = vi.fn();
    const recipients: Recipient[] = [
      { name: "Jane", email: "jane@x.com" },
      { name: "", email: "bob@x.com" },
    ];
    wrap(
      <RecipientsField
        label="To"
        fieldKey="to"
        recipients={recipients}
        onChange={onChange}
        testid="to"
      />,
    );
    fireEvent.click(screen.getAllByTestId("to-remove")[0]);
    expect(onChange).toHaveBeenCalledWith([{ name: "", email: "bob@x.com" }]);
  });

  it("moves a recipient from To to Cc via the action button", () => {
    const onMove = vi.fn();
    const r: Recipient = { name: "Jane", email: "jane@x.com" };
    wrap(
      <RecipientsField
        label="To"
        fieldKey="to"
        recipients={[r]}
        onChange={vi.fn()}
        onMove={onMove}
        moveTargets={[{ key: "cc", label: "Cc" }]}
        testid="to"
      />,
    );
    // Open the chip editor, then click "→ Cc".
    fireEvent.click(screen.getByTestId("to-chip"));
    fireEvent.click(screen.getByTestId("to-move-cc"));
    expect(onMove).toHaveBeenCalledWith("to", "cc", r);
  });
});
