import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  draftKey,
  draftPreview,
  getDraft,
  getTicketDrafts,
  saveDraft,
  clearDraft,
  resetDraftsForTest,
  useReplyDraft,
  useTicketReplyDrafts,
  type ReplyDraft,
} from "./replyDrafts";

const STORAGE_KEY = "tiqora-reply-drafts";

function makeDraft(overrides: Partial<Omit<ReplyDraft, "updatedAt">> = {}) {
  return {
    ticketId: 1,
    articleId: 10,
    replyAll: false,
    subject: "Re: Hello",
    body: "Thanks!",
    to: "a@x.com",
    cc: "",
    bcc: "",
    replyTo: "",
    aiDraftId: null,
    ...overrides,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  resetDraftsForTest();
});

describe("saveDraft / getDraft", () => {
  it("stores and retrieves a draft with updatedAt set", () => {
    const before = Date.now();
    saveDraft(makeDraft());
    const draft = getDraft(1, 10);
    expect(draft).not.toBeNull();
    expect(draft?.ticketId).toBe(1);
    expect(draft?.articleId).toBe(10);
    expect(draft?.body).toBe("Thanks!");
    expect(draft?.updatedAt).toBeGreaterThanOrEqual(before);
  });

  it("returns null for an unknown key", () => {
    expect(getDraft(999, 999)).toBeNull();
  });
});

describe("clearDraft", () => {
  it("removes a stored draft", () => {
    saveDraft(makeDraft());
    expect(getDraft(1, 10)).not.toBeNull();
    clearDraft(1, 10);
    expect(getDraft(1, 10)).toBeNull();
  });

  it("is a no-op for a key that does not exist", () => {
    expect(() => clearDraft(42, 42)).not.toThrow();
    expect(getDraft(42, 42)).toBeNull();
  });
});

describe("getTicketDrafts", () => {
  it("filters by ticketId, excluding drafts from other tickets", () => {
    saveDraft(makeDraft({ ticketId: 1, articleId: 1 }));
    saveDraft(makeDraft({ ticketId: 2, articleId: 2 }));
    saveDraft(makeDraft({ ticketId: 1, articleId: 3 }));

    const drafts = getTicketDrafts(1);
    expect(drafts).toHaveLength(2);
    expect(drafts.every((d) => d.ticketId === 1)).toBe(true);
  });

  it("sorts by updatedAt descending (newest first)", async () => {
    const now = Date.now();
    const older: ReplyDraft = { ...makeDraft({ ticketId: 1, articleId: 1 }), updatedAt: now - 1000 };
    const newest: ReplyDraft = { ...makeDraft({ ticketId: 1, articleId: 2 }), updatedAt: now };
    const middle: ReplyDraft = { ...makeDraft({ ticketId: 1, articleId: 3 }), updatedAt: now - 500 };
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        [draftKey(1, 1)]: older,
        [draftKey(1, 2)]: newest,
        [draftKey(1, 3)]: middle,
      }),
    );
    vi.resetModules();
    const mod = await import("./replyDrafts");
    const drafts = mod.getTicketDrafts(1);
    expect(drafts.map((d) => d.articleId)).toEqual([2, 3, 1]);
  });
});

describe("localStorage persistence", () => {
  it("writes valid JSON under the storage key after saveDraft", () => {
    saveDraft(makeDraft());
    const raw = window.localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw as string);
    expect(parsed[draftKey(1, 10)]).toBeDefined();
    expect(parsed[draftKey(1, 10)].body).toBe("Thanks!");
  });
});

describe("robustness against corrupted localStorage", () => {
  it("treats non-JSON content as an empty store", async () => {
    window.localStorage.setItem(STORAGE_KEY, "nicht-json");
    vi.resetModules();
    const mod = await import("./replyDrafts");
    expect(mod.getDraft(1, 10)).toBeNull();
    expect(mod.getTicketDrafts(1)).toEqual([]);
  });

  it("treats a JSON array as an empty store", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([1, 2, 3]));
    vi.resetModules();
    const mod = await import("./replyDrafts");
    expect(mod.getDraft(1, 10)).toBeNull();
    expect(mod.getTicketDrafts(1)).toEqual([]);
  });

  it("drops incomplete entries from an object payload", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        // Missing required fields (articleId/body/updatedAt).
        broken: { ticketId: 1, subject: "oops" },
        alsoBroken: "not-an-object",
      }),
    );
    vi.resetModules();
    const mod = await import("./replyDrafts");
    expect(mod.getDraft(1, 10)).toBeNull();
    expect(mod.getTicketDrafts(1)).toEqual([]);
  });
});

describe("draftPreview", () => {
  it("strips the quoted original (lines starting with >)", () => {
    const body = "My answer here.\n\nOn 12 Jul 2026, Thomas wrote:\n> original line 1\n> original line 2";
    expect(draftPreview(body)).toBe("My answer here.");
  });

  it("removes a German attribution line before the quote", () => {
    // ATTRIBUTION_RE requires the trigger word directly before the trailing
    // colon (mirrors the backend's "<who> wrote:" shape), so the German
    // equivalent is "<who> schrieb:", not "schrieb <who>:".
    const body = "Klar, mach ich.\n\nAm 12.07.2026, Thomas schrieb:\n> Originaltext";
    expect(draftPreview(body)).toBe("Klar, mach ich.");
  });

  it("truncates to maxChars with an ellipsis", () => {
    const body = "a".repeat(200);
    const preview = draftPreview(body, 50);
    expect(preview.length).toBe(51);
    expect(preview.endsWith("…")).toBe(true);
    expect(preview.startsWith("a".repeat(50))).toBe(true);
  });

  it("returns an empty string for a body consisting only of quote", () => {
    const body = "On 12 Jul 2026, Thomas wrote:\n> only quoted text\n> more quote";
    expect(draftPreview(body)).toBe("");
  });

  it("returns the full text unchanged when it fits and has no quote", () => {
    expect(draftPreview("Short answer.")).toBe("Short answer.");
  });
});

describe("useReplyDraft hook", () => {
  it("re-renders with the new value after saveDraft and clearDraft from outside", () => {
    const { result } = renderHook(() => useReplyDraft(1, 10));
    expect(result.current).toBeNull();

    act(() => {
      saveDraft(makeDraft());
    });
    expect(result.current).not.toBeNull();
    expect(result.current?.body).toBe("Thanks!");

    act(() => {
      clearDraft(1, 10);
    });
    expect(result.current).toBeNull();
  });
});

describe("useTicketReplyDrafts hook", () => {
  it("re-renders with updated draft lists after saveDraft and clearDraft from outside", () => {
    const { result } = renderHook(() => useTicketReplyDrafts(1));
    expect(result.current).toEqual([]);

    act(() => {
      saveDraft(makeDraft({ ticketId: 1, articleId: 1 }));
    });
    expect(result.current).toHaveLength(1);

    act(() => {
      saveDraft(makeDraft({ ticketId: 1, articleId: 2 }));
    });
    expect(result.current).toHaveLength(2);

    act(() => {
      clearDraft(1, 1);
    });
    expect(result.current).toHaveLength(1);
    expect(result.current[0].articleId).toBe(2);
  });
});
