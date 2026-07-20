import { describe, it, expect } from "vitest";
import {
  isNewTicketState,
  stateLabel,
  stateLabelI18nKey,
  stateNameKey,
} from "./status";

describe("stateNameKey", () => {
  it("maps stock Znuny compound names", () => {
    expect(stateNameKey("new")).toBe("new");
    expect(stateNameKey("open")).toBe("open");
    expect(stateNameKey("closed successful")).toBe("closedSuccessful");
    expect(stateNameKey("closed unsuccessful")).toBe("closedUnsuccessful");
    expect(stateNameKey("pending reminder")).toBe("pendingReminder");
    expect(stateNameKey("pending auto close+")).toBe("pendingAutoClosePlus");
    expect(stateNameKey("pending auto close-")).toBe("pendingAutoCloseMinus");
    expect(stateNameKey("merged")).toBe("merged");
    expect(stateNameKey("removed")).toBe("removed");
  });

  it("is case-insensitive and trims whitespace", () => {
    expect(stateNameKey("  Open  ")).toBe("open");
    expect(stateNameKey("CLOSED SUCCESSFUL")).toBe("closedSuccessful");
  });

  it("returns null for unknown names", () => {
    expect(stateNameKey(null)).toBeNull();
    expect(stateNameKey("")).toBeNull();
    expect(stateNameKey("custom workflow hold")).toBeNull();
  });
});

describe("stateLabel", () => {
  const t = (key: string, opts?: { defaultValue?: string }) => {
    const map: Record<string, string> = {
      "ticket.stateName.open": "Offen",
      "ticket.stateName.closedSuccessful": "Erfolgreich geschlossen",
      "ticket.stateName.pendingReminder": "Erinnerung wartend",
      "ticket.stateName.new": "Neu",
    };
    return map[key] ?? opts?.defaultValue ?? key;
  };

  it("returns the localised label for known states", () => {
    expect(stateLabel(t, "open")).toBe("Offen");
    expect(stateLabel(t, "closed successful")).toBe("Erfolgreich geschlossen");
    expect(stateLabel(t, "pending reminder")).toBe("Erinnerung wartend");
  });

  it("falls back to the raw name when unmapped", () => {
    expect(stateLabel(t, "custom hold")).toBe("custom hold");
  });

  it("uses the fallback for empty input", () => {
    expect(stateLabel(t, null)).toBe("—");
    expect(stateLabel(t, undefined, "n/a")).toBe("n/a");
  });

  it("exposes a full i18n key path", () => {
    expect(stateLabelI18nKey("open")).toBe("ticket.stateName.open");
    expect(stateLabelI18nKey("unknown-x")).toBeNull();
  });
});

describe("isNewTicketState", () => {
  it("detects new via state_type or state name", () => {
    expect(isNewTicketState("new", "new")).toBe(true);
    expect(isNewTicketState("new", null)).toBe(true);
    expect(isNewTicketState("open", "new")).toBe(true);
    expect(isNewTicketState("open", "open")).toBe(false);
    expect(isNewTicketState(null, null)).toBe(false);
  });
});
