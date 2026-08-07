import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { FormDraftOut } from "./formDraftApi";
import {
  draftKey,
  draftPreview,
  getDraft,
  useClearReplyDraft,
  useReplyDraft,
  useSaveReplyDraft,
  useTicketReplyDrafts,
  type ReplyDraft,
} from "./replyDrafts";

const { list, upsert, remove } = vi.hoisted(() => ({
  list: vi.fn(),
  upsert: vi.fn(),
  remove: vi.fn(),
}));

vi.mock("./formDraftApi", async () => {
  const actual =
    await vi.importActual<typeof import("./formDraftApi")>("./formDraftApi");
  return { ...actual, formDraftApi: { list, upsert, remove } };
});

const CONTENT = {
  replyAll: false,
  subject: "Re: Hello",
  body: "Thanks!",
  to: "a@x.com",
  cc: "",
  bcc: "",
  replyTo: "",
  aiDraftId: null,
};

function row(overrides: Partial<FormDraftOut> = {}): FormDraftOut {
  return {
    id: 1,
    ticket_id: 1,
    user_id: 1,
    action: "AgentTicketCompose",
    article_id: 10,
    title: null,
    content: JSON.stringify(CONTENT),
    created: "2026-08-07T10:00:00",
    changed: "2026-08-07T10:00:00",
    ...overrides,
  };
}

function draftInput(
  overrides: Partial<Omit<ReplyDraft, "updatedAt">> = {},
): Omit<ReplyDraft, "updatedAt"> {
  return { ticketId: 1, articleId: 10, ...CONTENT, ...overrides };
}

let qc: QueryClient;

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  list.mockReset().mockResolvedValue([]);
  upsert.mockReset().mockImplementation((_t, body) =>
    Promise.resolve(row({ article_id: body.article_id, content: body.content })),
  );
  remove.mockReset().mockResolvedValue(undefined);
});

describe("draftKey", () => {
  it("combines ticket and article", () => {
    expect(draftKey(1, 10)).toBe("1:10");
  });
});

describe("useTicketReplyDrafts", () => {
  it("loads the ticket's drafts from the server", async () => {
    list.mockResolvedValue([row()]);
    const { result } = renderHook(() => useTicketReplyDrafts(1), { wrapper });

    await waitFor(() => expect(result.current).toHaveLength(1));
    expect(result.current[0]).toMatchObject({
      ticketId: 1,
      articleId: 10,
      body: "Thanks!",
      to: "a@x.com",
    });
    expect(list).toHaveBeenCalledWith(1, expect.anything());
  });

  it("sorts by last edit, newest first", async () => {
    list.mockResolvedValue([
      row({ id: 1, article_id: 1, changed: "2026-08-07T10:00:00" }),
      row({ id: 2, article_id: 2, changed: "2026-08-07T12:00:00" }),
      row({ id: 3, article_id: 3, changed: "2026-08-07T11:00:00" }),
    ]);
    const { result } = renderHook(() => useTicketReplyDrafts(1), { wrapper });

    await waitFor(() => expect(result.current).toHaveLength(3));
    expect(result.current.map((d) => d.articleId)).toEqual([2, 3, 1]);
  });

  it("skips rows that are not reply drafts", async () => {
    list.mockResolvedValue([
      row({ id: 1, action: "AgentTicketNote" }),
      // Ticket-wide (no article): not a reply draft either.
      row({ id: 2, article_id: null }),
      row({ id: 3, article_id: 11 }),
    ]);
    const { result } = renderHook(() => useTicketReplyDrafts(1), { wrapper });

    await waitFor(() => expect(result.current).toHaveLength(1));
    expect(result.current[0].articleId).toBe(11);
  });

  it("drops rows whose content is not a usable draft", async () => {
    list.mockResolvedValue([
      row({ id: 1, article_id: 1, content: "nicht-json" }),
      row({ id: 2, article_id: 2, content: JSON.stringify([1, 2, 3]) }),
      row({ id: 3, article_id: 3, content: JSON.stringify({ subject: "no body" }) }),
    ]);
    const { result } = renderHook(() => useTicketReplyDrafts(1), { wrapper });

    await waitFor(() => expect(list).toHaveBeenCalled());
    await waitFor(() => expect(result.current).toEqual([]));
  });
});

describe("useReplyDraft", () => {
  it("picks out one article's draft and ignores the others", async () => {
    list.mockResolvedValue([row({ id: 1, article_id: 10 }), row({ id: 2, article_id: 20 })]);
    const { result } = renderHook(() => useReplyDraft(1, 20), { wrapper });

    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current?.articleId).toBe(20);
  });

  it("is null for an article without a draft", async () => {
    list.mockResolvedValue([row({ article_id: 10 })]);
    const { result } = renderHook(() => useReplyDraft(1, 99), { wrapper });

    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });
});

describe("useSaveReplyDraft", () => {
  it("shows the draft before the server has answered, and persists it", async () => {
    // Never resolves: whatever shows up does so optimistically.
    upsert.mockImplementationOnce(() => new Promise<FormDraftOut>(() => {}));

    const { result } = renderHook(
      () => ({ save: useSaveReplyDraft(), drafts: useTicketReplyDrafts(1) }),
      { wrapper },
    );
    await waitFor(() => expect(list).toHaveBeenCalled());

    const before = Date.now();
    act(() => result.current.save(draftInput()));

    await waitFor(() => expect(result.current.drafts).toHaveLength(1));
    expect(result.current.drafts[0].body).toBe("Thanks!");
    expect(result.current.drafts[0].updatedAt).toBeGreaterThanOrEqual(before);

    expect(upsert).toHaveBeenCalledWith(1, {
      action: "AgentTicketCompose",
      article_id: 10,
      content: JSON.stringify(CONTENT),
    });
  });

  it("replaces the draft of the same article instead of adding one", async () => {
    const { result } = renderHook(
      () => ({ save: useSaveReplyDraft(), drafts: useTicketReplyDrafts(1) }),
      { wrapper },
    );
    await waitFor(() => expect(list).toHaveBeenCalled());

    act(() => result.current.save(draftInput({ body: "first" })));
    act(() => result.current.save(draftInput({ body: "second" })));

    await waitFor(() => expect(result.current.drafts).toHaveLength(1));
    expect(result.current.drafts[0].body).toBe("second");
  });

  it("does not roll a newer edit back to the saved response", async () => {
    let resolveUpsert: (value: FormDraftOut) => void = () => {};
    upsert.mockImplementationOnce(
      () => new Promise<FormDraftOut>((res) => (resolveUpsert = res)),
    );

    const { result } = renderHook(
      () => ({ save: useSaveReplyDraft(), drafts: useTicketReplyDrafts(1) }),
      { wrapper },
    );
    await waitFor(() => expect(list).toHaveBeenCalled());

    act(() => result.current.save(draftInput({ body: "first" })));
    act(() => result.current.save(draftInput({ body: "second" })));
    // The first save now lands — it must not resurrect "first".
    await act(async () => {
      resolveUpsert(row({ content: JSON.stringify({ ...CONTENT, body: "first" }) }));
    });

    expect(result.current.drafts[0].body).toBe("second");
  });
});

describe("useClearReplyDraft", () => {
  it("removes the draft locally and on the server", async () => {
    list.mockResolvedValue([row({ article_id: 10 })]);
    const { result } = renderHook(
      () => ({ clear: useClearReplyDraft(), drafts: useTicketReplyDrafts(1) }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.drafts).toHaveLength(1));

    act(() => result.current.clear(1, 10));

    await waitFor(() => expect(result.current.drafts).toEqual([]));
    expect(remove).toHaveBeenCalledWith(1, "AgentTicketCompose", 10);
  });

  it("does not call the server for a draft that is not there", async () => {
    const { result } = renderHook(() => useClearReplyDraft(), { wrapper });
    act(() => result.current(42, 42));
    expect(remove).not.toHaveBeenCalled();
  });
});

describe("getDraft", () => {
  it("reads a loaded draft straight out of the cache", async () => {
    list.mockResolvedValue([row({ article_id: 10 })]);
    const { result } = renderHook(() => useTicketReplyDrafts(1), { wrapper });
    await waitFor(() => expect(result.current).toHaveLength(1));

    expect(getDraft(qc, 1, 10)?.body).toBe("Thanks!");
    expect(getDraft(qc, 1, 99)).toBeNull();
    expect(getDraft(qc, 999, 10)).toBeNull();
  });
});

describe("draftPreview", () => {
  it("strips the quoted original (lines starting with >)", () => {
    const body =
      "My answer here.\n\nOn 12 Jul 2026, Thomas wrote:\n> original line 1\n> original line 2";
    expect(draftPreview(body)).toBe("My answer here.");
  });

  it("removes a German attribution line before the quote", () => {
    const body = "Klar, mach ich.\n\nAm 12.07.2026 schrieb Thomas:\n> Originaltext";
    expect(draftPreview(body)).toBe("Klar, mach ich.");
  });

  it("truncates to maxChars with an ellipsis", () => {
    const preview = draftPreview("a".repeat(200), 50);
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
