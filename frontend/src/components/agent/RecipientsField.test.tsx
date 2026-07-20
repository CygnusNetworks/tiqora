import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import {
  RecipientsField,
  parseRecipient,
  parseRecipientList,
  joinRecipients,
  moveRecipientBetween,
  sameRecipient,
  type Recipient,
} from "./RecipientsField";

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

const DND_MIME = "application/x-tiqora-recipient";

function makeDataTransfer(initial?: Record<string, string>) {
  const store: Record<string, string> = { ...(initial ?? {}) };
  return {
    store,
    setData(type: string, val: string) {
      store[type] = val;
    },
    getData(type: string) {
      return store[type] ?? "";
    },
    get types() {
      return Object.keys(store);
    },
    effectAllowed: "all" as string,
  };
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

describe("moveRecipientBetween", () => {
  it("removes from source and appends to target (move, not copy)", () => {
    const r: Recipient = { name: "Jane", email: "jane@x.com" };
    // Rehydrated drop payload is a *new* object — identity must be by email.
    const dropped: Recipient = { name: "Jane", email: "jane@x.com" };
    const { source, target } = moveRecipientBetween([r], [], dropped);
    expect(source).toEqual([]);
    expect(target).toEqual([dropped]);
  });

  it("dedupes when the address already exists in the target", () => {
    const r: Recipient = { name: "Jane", email: "jane@x.com" };
    const existing: Recipient = { name: "J.", email: "JANE@x.com" };
    const { source, target } = moveRecipientBetween([r], [existing], {
      name: "Jane",
      email: "jane@x.com",
    });
    expect(source).toEqual([]);
    expect(target).toEqual([existing]);
  });

  it("sameRecipient matches case-insensitively", () => {
    expect(
      sameRecipient(
        { name: "A", email: "A@x.com" },
        { name: "B", email: "a@x.com" },
      ),
    ).toBe(true);
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

  it("drag-move removes from source and adds to target (not a copy)", () => {
    const r: Recipient = { name: "Jane", email: "jane@x.com" };

    function Harness() {
      const [to, setTo] = useState<Recipient[]>([r]);
      const [cc, setCc] = useState<Recipient[]>([]);
      const onMove = (from: string, dest: string, rec: Recipient) => {
        if (from === "to" && dest === "cc") {
          const { source, target } = moveRecipientBetween(to, cc, rec);
          setTo(source);
          setCc(target);
        }
      };
      return (
        <>
          <RecipientsField
            label="To"
            fieldKey="to"
            recipients={to}
            onChange={setTo}
            onMove={onMove}
            testid="to"
          />
          <RecipientsField
            label="Cc"
            fieldKey="cc"
            recipients={cc}
            onChange={setCc}
            onMove={onMove}
            testid="cc"
          />
        </>
      );
    }

    wrap(<Harness />);
    expect(screen.getByTestId("to-chip")).toBeTruthy();
    expect(screen.queryByTestId("cc-chip")).toBeNull();

    const dt = makeDataTransfer({
      [DND_MIME]: JSON.stringify({
        from: "to",
        // New object, as produced by JSON rehydrate on drop.
        recipient: { name: "Jane", email: "jane@x.com" },
      }),
    });
    fireEvent.drop(screen.getByTestId("cc"), { dataTransfer: dt });

    expect(screen.queryByTestId("to-chip")).toBeNull();
    expect(screen.getByTestId("cc-chip")).toBeTruthy();
    expect(screen.getByTestId("cc-chip").textContent).toContain("Jane");
  });

  it("shows a danger border and hint when required To is empty", () => {
    wrap(
      <RecipientsField
        label="To"
        fieldKey="to"
        recipients={[]}
        onChange={vi.fn()}
        required
        testid="to"
      />,
    );
    const field = screen.getByTestId("to");
    expect(field.getAttribute("data-empty-required")).toBe("true");
    expect(field.className).toMatch(/border-danger/);
    expect(screen.getByTestId("to-required-hint")).toBeTruthy();
  });

  it("does not show the danger border when required To has addresses", () => {
    wrap(
      <RecipientsField
        label="To"
        fieldKey="to"
        recipients={[{ name: "", email: "a@x.com" }]}
        onChange={vi.fn()}
        required
        testid="to"
      />,
    );
    const field = screen.getByTestId("to");
    expect(field.getAttribute("data-empty-required")).toBeNull();
    expect(field.className).not.toMatch(/border-danger/);
  });

  it("confirms chip edits via the OK button and closes the editor", () => {
    const onChange = vi.fn();
    const r: Recipient = { name: "Jane", email: "jane@x.com" };
    wrap(
      <RecipientsField
        label="To"
        fieldKey="to"
        recipients={[r]}
        onChange={onChange}
        testid="to"
      />,
    );
    fireEvent.click(screen.getByTestId("to-chip"));
    expect(screen.getByTestId("to-editor-confirm")).toBeTruthy();
    // No move buttons (drag-only).
    expect(screen.queryByTestId("to-move-cc")).toBeNull();

    fireEvent.change(screen.getByTestId("to-name"), {
      target: { value: "Janet" },
    });
    fireEvent.change(screen.getByTestId("to-email"), {
      target: { value: "janet@x.com" },
    });
    // Until confirm, parent is not updated.
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("to-editor-confirm"));
    expect(onChange).toHaveBeenCalledWith([
      { name: "Janet", email: "janet@x.com" },
    ]);
    // Editor closed.
    expect(screen.queryByTestId("to-editor-confirm")).toBeNull();
  });

  it("Cc toggle expands, collapses, and shows a count badge when collapsed with addresses", () => {
    // Mirrors ReplyDialog: true expand/collapse toggle; count badge only when
    // collapsed and non-empty. Addresses stay in state while hidden.
    function CcCollapseHarness({ initialCc }: { initialCc: Recipient[] }) {
      const [cc, setCc] = useState(initialCc);
      const [showCc, setShowCc] = useState(initialCc.length > 0);
      return (
        <div>
          {showCc && (
            <RecipientsField
              label="Cc"
              fieldKey="cc"
              recipients={cc}
              onChange={setCc}
              testid="cc"
            />
          )}
          <button
            type="button"
            data-testid="reply-toggle-cc"
            aria-expanded={showCc}
            onClick={() => setShowCc((v) => !v)}
          >
            Cc
            {!showCc && cc.length > 0 && (
              <span data-testid="reply-toggle-cc-count">{cc.length}</span>
            )}
          </button>
        </div>
      );
    }

    const { unmount } = wrap(<CcCollapseHarness initialCc={[]} />);
    expect(screen.queryByTestId("cc")).toBeNull();
    expect(screen.getByTestId("reply-toggle-cc")).toBeTruthy();
    expect(screen.queryByTestId("reply-toggle-cc-count")).toBeNull();
    fireEvent.click(screen.getByTestId("reply-toggle-cc"));
    expect(screen.getByTestId("cc")).toBeTruthy();
    // Expanded: no count badge.
    expect(screen.queryByTestId("reply-toggle-cc-count")).toBeNull();
    // Collapse empty field again — still no badge.
    fireEvent.click(screen.getByTestId("reply-toggle-cc"));
    expect(screen.queryByTestId("cc")).toBeNull();
    expect(screen.queryByTestId("reply-toggle-cc-count")).toBeNull();
    unmount();

    // With addresses, starts expanded; collapse shows count badge; addresses remain.
    wrap(
      <CcCollapseHarness
        initialCc={[
          { name: "", email: "cc1@x.com" },
          { name: "", email: "cc2@x.com" },
        ]}
      />,
    );
    expect(screen.getByTestId("cc")).toBeTruthy();
    expect(screen.getAllByTestId("cc-chip")).toHaveLength(2);
    fireEvent.click(screen.getByTestId("reply-toggle-cc"));
    expect(screen.queryByTestId("cc")).toBeNull();
    expect(screen.getByTestId("reply-toggle-cc-count").textContent).toBe("2");
    // Re-expand still has both chips (addresses not cleared).
    fireEvent.click(screen.getByTestId("reply-toggle-cc"));
    expect(screen.getByTestId("cc")).toBeTruthy();
    expect(screen.getAllByTestId("cc-chip")).toHaveLength(2);
    expect(screen.queryByTestId("reply-toggle-cc-count")).toBeNull();
  });
});
