import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { telegramChatKey } from "@/lib/telegramChatApi";
import { requestComposer } from "./composerBus";
import { TelegramChatComposer } from "./TelegramChatComposer";

const {
  createArticle,
  postTelegramTyping,
  listTemplates,
  getTelegramChat,
  getArticleBody,
  acquireTicketLock,
  getTicket,
  listReferenceStates,
  getState,
  formDrafts,
} = vi.hoisted(() => ({
  createArticle: vi.fn(),
  postTelegramTyping: vi.fn(),
  listTemplates: vi.fn(),
  getTelegramChat: vi.fn(),
  getArticleBody: vi.fn(),
  acquireTicketLock: vi.fn(),
  getTicket: vi.fn(),
  listReferenceStates: vi.fn(),
  getState: vi.fn(),
  formDrafts: { list: vi.fn(), upsert: vi.fn(), remove: vi.fn() },
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      createArticle,
      postTelegramTyping,
      listTemplates,
      getTelegramChat,
      getArticleBody,
      acquireTicketLock,
      getTicket,
      listReferenceStates,
    },
  };
});

vi.mock("@/lib/ticketAiApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ticketAiApi")>("@/lib/ticketAiApi");
  return { ...actual, ticketAiApi: { getState } };
});

vi.mock("@/lib/formDraftApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/formDraftApi")>("@/lib/formDraftApi");
  return { ...actual, formDraftApi: formDrafts };
});

const STATES = [
  { id: 4, name: "open", type_name: "open" },
  { id: 2, name: "closed successful", type_name: "closed" },
  { id: 6, name: "pending reminder", type_name: "pending reminder" },
];
const perms = (rw: boolean) => ({
  ro: true,
  move_into: rw,
  create: rw,
  note: true,
  owner: rw,
  priority: rw,
  rw,
});

let qc: QueryClient;

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  createArticle.mockReset().mockResolvedValue({ article_id: 99 });
  postTelegramTyping.mockReset().mockResolvedValue(undefined);
  listTemplates.mockReset().mockResolvedValue([]);
  getTelegramChat.mockReset().mockResolvedValue({
    chat_id: 1,
    display_name: "Jona",
    username: "jona",
    customer_user_login: null,
    identity_verified: true,
    consent_time: null,
    ai_escalated_at: null,
    messages: [],
  });
  getArticleBody.mockReset().mockResolvedValue({ body: "Mein Router blinkt rot", is_html: false });
  acquireTicketLock.mockReset().mockResolvedValue({ result: "acquired" });
  getTicket.mockReset().mockResolvedValue({ id: 1, permissions: perms(true) });
  listReferenceStates.mockReset().mockResolvedValue(STATES);
  getState.mockReset().mockResolvedValue({ drafts: [] });
  formDrafts.list.mockReset().mockResolvedValue([]);
  formDrafts.upsert.mockReset().mockImplementation((ticketId, body) =>
    Promise.resolve({
      id: 1,
      ticket_id: ticketId,
      user_id: 1,
      action: body.action,
      article_id: body.article_id,
      title: null,
      content: body.content,
      created: "2026-09-28T10:00:00",
      changed: "2026-09-28T10:00:00",
    }),
  );
  formDrafts.remove.mockReset().mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
});

function wrap(ui: React.ReactElement) {
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

async function mount(props: Partial<React.ComponentProps<typeof TelegramChatComposer>> = {}) {
  wrap(<TelegramChatComposer ticketId={1} {...props} />);
  const input = (await screen.findByTestId("tg-composer-input")) as HTMLTextAreaElement;
  // The stored draft decides the seed — wait for it so typing isn't overwritten.
  await waitFor(() => expect(formDrafts.list).toHaveBeenCalled());
  return input;
}

function type(input: HTMLTextAreaElement, value: string) {
  fireEvent.change(input, { target: { value, selectionStart: value.length } });
}

function lastPayload() {
  const calls = createArticle.mock.calls;
  return calls[calls.length - 1][1];
}

describe("TelegramChatComposer: sending", () => {
  it("Enter sends, Shift+Enter and IME composition do not", async () => {
    const input = await mount();
    type(input, "Hallo Jona");
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(createArticle).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(createArticle).toHaveBeenCalledTimes(1));
    expect(createArticle.mock.calls[0][0]).toBe(1);
    expect(lastPayload()).toMatchObject({
      sender_type: "agent",
      subject: "",
      body: "Hallo Jona",
      channel: "telegram",
      is_visible_for_customer: true,
      telegram_reply_to_article_id: null,
      ai_draft_id: null,
    });
  });

  it("clears the composer after a successful send and refreshes the chat", async () => {
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    const input = await mount();
    type(input, "Hallo");
    input.focus();
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(input.value).toBe(""));
    expect(document.activeElement).toBe(input);
    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(telegramChatKey(1)));
    expect(keys).toContain(JSON.stringify(["tickets", 1, "articles"]));
  });

  it("blocks sending above 4096 characters and marks the counter", async () => {
    const input = await mount();
    type(input, "a".repeat(4096));
    expect(screen.getByTestId("tg-composer-counter").textContent).toBe("4096 / 4096");
    expect(screen.getByTestId("tg-composer-send")).not.toBeDisabled();

    type(input, "a".repeat(4097));
    const counter = screen.getByTestId("tg-composer-counter");
    expect(counter.textContent).toBe("4097 / 4096");
    expect(counter.className).toContain("text-danger");
    expect(screen.getByTestId("tg-composer-send")).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(createArticle).not.toHaveBeenCalled();
  });

  it("keeps the text on failure, shows the server's reason and retries", async () => {
    createArticle.mockRejectedValueOnce(
      new ApiError(409, { detail: "Telegram: Chat nicht gefunden" }, "/x"),
    );
    const input = await mount();
    type(input, "Wichtige Antwort");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    expect(await screen.findByTestId("tg-composer-error")).toHaveTextContent(
      "Telegram: Chat nicht gefunden",
    );
    expect(input.value).toBe("Wichtige Antwort");

    fireEvent.click(screen.getByTestId("tg-composer-retry"));
    await waitFor(() => expect(createArticle).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(input.value).toBe(""));
    expect(screen.queryByTestId("tg-composer-error")).toBeNull();
  });

  it("sends the chosen next state", async () => {
    const input = await mount();
    fireEvent.click(await screen.findByTestId("tg-composer-next-closed"));
    type(input, "Erledigt");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(lastPayload()).toMatchObject({ state_id: 2, pending_time: null });
  });

  it("hides the next-state segment without rw", async () => {
    getTicket.mockResolvedValue({ id: 1, permissions: perms(false) });
    await mount();
    await waitFor(() => expect(getTicket).toHaveBeenCalled());
    expect(screen.queryByTestId("tg-composer-next-closed")).toBeNull();
  });

  it("reports composing while there is text", async () => {
    const onComposingChange = vi.fn();
    const input = await mount({ onComposingChange });
    type(input, "x");
    await waitFor(() => expect(onComposingChange).toHaveBeenLastCalledWith(true));
    type(input, "");
    await waitFor(() => expect(onComposingChange).toHaveBeenLastCalledWith(false));
  });
});

describe("TelegramChatComposer: snippets", () => {
  it("inserts a Chat template picked after a slash", async () => {
    listTemplates.mockResolvedValue([
      { id: 11, name: "Gruß", text: "Viele Grüße, dein Support-Team", content_type: "text/plain" },
      { id: 12, name: "Neustart", text: "Starte bitte den Router neu.", content_type: "text/plain" },
    ]);
    const input = await mount();
    type(input, "Danke! /neu");
    await screen.findByTestId("tg-snippet-picker");
    expect(listTemplates).toHaveBeenCalledWith(1, "Chat");
    expect(screen.queryByTestId("tg-snippet-11")).toBeNull();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(input.value).toBe("Danke! Starte bitte den Router neu.");
    expect(screen.queryByTestId("tg-snippet-picker")).toBeNull();
    // Enter picked the snippet — it must not have sent the message.
    expect(createArticle).not.toHaveBeenCalled();
  });

  it("closes the picker on Escape and ignores a slash inside a word", async () => {
    listTemplates.mockResolvedValue([{ id: 11, name: "Gruß", text: "Tschüss", content_type: "text/plain" }]);
    const input = await mount();
    type(input, "und/oder");
    await waitFor(() => expect(listTemplates).toHaveBeenCalled());
    expect(screen.queryByTestId("tg-snippet-picker")).toBeNull();

    type(input, "/");
    await screen.findByTestId("tg-snippet-picker");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByTestId("tg-snippet-picker")).toBeNull();
  });
});

describe("TelegramChatComposer: attachments", () => {
  it("sends attachments as base64, text may be empty", async () => {
    await mount();
    const file = new File(["hello"], "log.txt", { type: "text/plain" });
    fireEvent.change(screen.getByTestId("tg-composer-file"), { target: { files: [file] } });
    const chip = await screen.findByTestId("tg-composer-attachment-0");
    expect(chip).toHaveTextContent("log.txt");
    await waitFor(() => expect(screen.getByTestId("tg-composer-send")).not.toBeDisabled());

    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(lastPayload()).toMatchObject({
      body: "",
      attachments: [{ filename: "log.txt", content_type: "text/plain", content_base64: "aGVsbG8=" }],
    });
    await waitFor(() => expect(screen.queryByTestId("tg-composer-attachment-0")).toBeNull());
  });

  it("checks the cap against both of two adds in the same tick", async () => {
    await mount();
    const a = new File(["a"], "a.bin", { type: "application/octet-stream" });
    const b = new File(["b"], "b.bin", { type: "application/octet-stream" });
    Object.defineProperty(a, "size", { value: 10 * 1024 * 1024 });
    Object.defineProperty(b, "size", { value: 10 * 1024 * 1024 });
    const fileInput = screen.getByTestId("tg-composer-file");
    act(() => {
      fireEvent.change(fileInput, { target: { files: [a] } });
      fireEvent.change(fileInput, { target: { files: [b] } });
    });
    expect(await screen.findByTestId("tg-composer-attach-error")).toBeTruthy();
    expect(screen.getByTestId("tg-composer-attachment-0")).toHaveTextContent("a.bin");
    expect(screen.queryByTestId("tg-composer-attachment-1")).toBeNull();
  });

  it("names a 413 from the proxy instead of a generic failure", async () => {
    createArticle.mockRejectedValueOnce(new ApiError(413, "<html>413 Request Entity Too Large</html>", "/x"));
    const input = await mount();
    type(input, "mit Anhang");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    const error = await screen.findByTestId("tg-composer-error");
    expect(error).toHaveTextContent(i18n.t("ticket.telegram.composer.attachTooLargeServer"));
    expect(error).not.toHaveTextContent("html");
  });

  it("keeps text and files added while a send was in flight", async () => {
    let resolveSend: (v: unknown) => void = () => undefined;
    createArticle.mockImplementationOnce(() => new Promise((r) => (resolveSend = r)));
    const input = await mount();
    const fileInput = screen.getByTestId("tg-composer-file");
    type(input, "erste Nachricht");
    fireEvent.change(fileInput, { target: { files: [new File(["1"], "first.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(screen.getByTestId("tg-composer-send")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());

    type(input, "zweite Nachricht");
    fireEvent.change(fileInput, { target: { files: [new File(["2"], "second.txt", { type: "text/plain" })] } });
    await screen.findByTestId("tg-composer-attachment-1");
    await act(async () => resolveSend({ article_id: 99 }));

    await waitFor(() => expect(screen.queryByTestId("tg-composer-attachment-1")).toBeNull());
    expect(input.value).toBe("zweite Nachricht");
    expect(screen.getByTestId("tg-composer-attachment-0")).toHaveTextContent("second.txt");
    expect(lastPayload().attachments).toEqual([
      { filename: "first.txt", content_type: "text/plain", content_base64: "MQ==" },
    ]);
  });

  it("removes an attachment chip", async () => {
    await mount();
    const file = new File(["x"], "a.png", { type: "image/png" });
    fireEvent.change(screen.getByTestId("tg-composer-file"), { target: { files: [file] } });
    await screen.findByTestId("tg-composer-attachment-0");
    fireEvent.click(screen.getByTestId("tg-composer-attachment-remove-0"));
    expect(screen.queryByTestId("tg-composer-attachment-0")).toBeNull();
  });

  it("rejects files above 18 MB in total", async () => {
    await mount();
    const big = new File(["x"], "video.mp4", { type: "video/mp4" });
    Object.defineProperty(big, "size", { value: 18 * 1024 * 1024 + 1 });
    fireEvent.change(screen.getByTestId("tg-composer-file"), { target: { files: [big] } });
    expect(await screen.findByTestId("tg-composer-attach-error")).toBeTruthy();
    expect(screen.queryByTestId("tg-composer-attachment-0")).toBeNull();
    expect(screen.getByTestId("tg-composer-send")).toBeDisabled();
  });
});

describe("TelegramChatComposer: quote, buttons, AI", () => {
  it("takes a quote from the composer bus and sends its id", async () => {
    const input = await mount();
    act(() => requestComposer(1, { quoteArticleId: 5, focus: true }));
    const chip = await screen.findByTestId("tg-composer-quote");
    await waitFor(() => expect(chip).toHaveTextContent("Mein Router blinkt rot"));
    expect(document.activeElement).toBe(input);

    type(input, "Schau ich mir an");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(lastPayload().telegram_reply_to_article_id).toBe(5);
    await waitFor(() => expect(screen.queryByTestId("tg-composer-quote")).toBeNull());
  });

  it("drops the quote on ×", async () => {
    const input = await mount();
    act(() => requestComposer(1, { quoteArticleId: 5 }));
    fireEvent.click(await screen.findByTestId("tg-composer-quote-remove"));
    type(input, "ohne Zitat");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(lastPayload().telegram_reply_to_article_id).toBeNull();
  });

  it("sends the 'Problem gelöst?' preset buttons", async () => {
    const input = await mount();
    fireEvent.click(screen.getByTestId("tg-composer-buttons-toggle"));
    fireEvent.click(screen.getByTestId("tg-buttons-preset"));
    expect(input.value).toBe("Ist dein Problem damit gelöst?");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(lastPayload().telegram_buttons).toEqual([
      { label: "Ja", action: "resolve_yes" },
      { label: "Nein", action: "resolve_no" },
    ]);
  });

  it("keeps typed text when applying the preset and sends free labels as reply buttons", async () => {
    const input = await mount();
    type(input, "Passt der Termin?");
    fireEvent.click(screen.getByTestId("tg-composer-buttons-toggle"));
    fireEvent.click(screen.getByTestId("tg-buttons-add"));
    fireEvent.change(screen.getByTestId("tg-buttons-label-0"), { target: { value: "Morgen" } });
    expect(input.value).toBe("Passt der Termin?");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(lastPayload().telegram_buttons).toEqual([{ label: "Morgen", action: "reply" }]);
  });

  it("removes a single button, clears all, and drops an empty one on Backspace", async () => {
    await mount();
    fireEvent.click(screen.getByTestId("tg-composer-buttons-toggle"));
    fireEvent.click(screen.getByTestId("tg-buttons-preset"));
    expect(screen.getByTestId("tg-buttons-label-1")).toHaveValue("Nein");

    fireEvent.click(screen.getByTestId("tg-buttons-remove-0"));
    expect(screen.getByTestId("tg-buttons-label-0")).toHaveValue("Nein");
    expect(screen.queryByTestId("tg-buttons-label-1")).toBeNull();

    fireEvent.click(screen.getByTestId("tg-buttons-add"));
    const empty = screen.getByTestId("tg-buttons-label-1");
    fireEvent.keyDown(empty, { key: "Backspace" });
    expect(screen.queryByTestId("tg-buttons-label-1")).toBeNull();
    // A label with text is edited by Backspace, not removed.
    fireEvent.keyDown(screen.getByTestId("tg-buttons-label-0"), { key: "Backspace" });
    expect(screen.getByTestId("tg-buttons-label-0")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("tg-buttons-clear"));
    expect(screen.queryByTestId("tg-buttons-label-0")).toBeNull();
    expect(screen.queryByTestId("tg-buttons-clear")).toBeNull();
  });

  it("turning the Buttons toggle off discards the buttons", async () => {
    const input = await mount();
    const toggle = screen.getByTestId("tg-composer-buttons-toggle");
    fireEvent.click(toggle);
    fireEvent.click(screen.getByTestId("tg-buttons-preset"));
    fireEvent.click(toggle);
    expect(screen.queryByTestId("tg-button-editor")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.queryByTestId("tg-buttons-label-0")).toBeNull();

    fireEvent.click(toggle);
    type(input, "Ohne Buttons");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(lastPayload().telegram_buttons).toBeUndefined();
  });

  it("asks before the AI suggestion replaces typed text", async () => {
    getState.mockResolvedValue({
      drafts: [{ id: 3, status: "open", kind: "reply", body: "KI-Text" }],
    });
    const input = await mount();
    await screen.findByTestId("tg-composer-ai");
    type(input, "mein eigener Text");

    fireEvent.click(screen.getByTestId("tg-composer-ai-take"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-cancel"));
    await waitFor(() => expect(screen.queryByTestId("confirm-dialog")).toBeNull());
    expect(input.value).toBe("mein eigener Text");

    fireEvent.click(screen.getByTestId("tg-composer-ai-take"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(input.value).toBe("KI-Text"));
  });

  it("applies a draft handed over from the composer bus and sends its id", async () => {
    const input = await mount();
    act(() =>
      requestComposer(1, { draft: { id: 7, body: "Vom Panel übernommener Entwurf" }, focus: true }),
    );
    await waitFor(() => expect(input.value).toBe("Vom Panel übernommener Entwurf"));
    expect(document.activeElement).toBe(input);

    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(lastPayload().ai_draft_id).toBe(7);
  });

  it("asks before a handed-over draft replaces typed text", async () => {
    const input = await mount();
    type(input, "mein eigener Text");

    act(() => requestComposer(1, { draft: { id: 7, body: "Vom Panel übernommener Entwurf" } }));
    fireEvent.click(await screen.findByTestId("confirm-dialog-cancel"));
    await waitFor(() => expect(screen.queryByTestId("confirm-dialog")).toBeNull());
    expect(input.value).toBe("mein eigener Text");

    act(() => requestComposer(1, { draft: { id: 7, body: "Vom Panel übernommener Entwurf" } }));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(input.value).toBe("Vom Panel übernommener Entwurf"));
  });

  it("offers an open AI draft and sends its id", async () => {
    getState.mockResolvedValue({
      drafts: [
        { id: 3, status: "open", kind: "reply", body: "Danke dir! Starte den Router neu." },
        { id: 2, status: "accepted", kind: "reply", body: "alt" },
      ],
    });
    const input = await mount();
    const bar = await screen.findByTestId("tg-composer-ai");
    expect(bar).toHaveTextContent("Danke dir! Starte den Router neu.");
    fireEvent.click(screen.getByTestId("tg-composer-ai-take"));
    expect(input.value).toBe("Danke dir! Starte den Router neu.");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(lastPayload().ai_draft_id).toBe(3);
  });
});

describe("TelegramChatComposer: typing ping", () => {
  it("pings at most every 4 seconds while typing", async () => {
    const input = await mount();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-28T10:00:00Z"));
    type(input, "H");
    type(input, "Ha");
    expect(postTelegramTyping).toHaveBeenCalledTimes(1);
    expect(postTelegramTyping).toHaveBeenCalledWith(1);

    vi.setSystemTime(new Date("2026-09-28T10:00:03Z"));
    type(input, "Hal");
    expect(postTelegramTyping).toHaveBeenCalledTimes(1);

    vi.setSystemTime(new Date("2026-09-28T10:00:04.100Z"));
    type(input, "Hall");
    expect(postTelegramTyping).toHaveBeenCalledTimes(2);
  });
});

describe("TelegramChatComposer: draft", () => {
  it("restores the stored chat draft and autosaves edits", async () => {
    formDrafts.list.mockResolvedValue([
      {
        id: 9,
        ticket_id: 1,
        user_id: 1,
        action: "TelegramChat",
        article_id: null,
        title: null,
        content: JSON.stringify({ body: "angefangen", quoteArticleId: null, aiDraftId: null }),
        created: "2026-09-28T10:00:00",
        changed: "2026-09-28T10:00:00",
      },
    ]);
    const input = await mount();
    await waitFor(() => expect(input.value).toBe("angefangen"));
    type(input, "angefangen und weiter");
    await waitFor(() =>
      expect(formDrafts.upsert).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ action: "TelegramChat", article_id: null }),
      ),
    );
  });
});

describe("TelegramChatComposer: unclear and malformed failures", () => {
  it("treats a gateway HTML error as unclear: no instant resend, text kept, history refreshed", async () => {
    createArticle.mockRejectedValueOnce(
      new ApiError(504, "<html><body><h1>504 Gateway Time-out</h1></body></html>", "/x"),
    );
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    const input = await mount();
    type(input, "Vielleicht schon raus");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    const error = await screen.findByTestId("tg-composer-error");
    expect(error).toHaveTextContent(i18n.t("ticket.telegram.composer.sendUncertain"));
    expect(error).not.toHaveTextContent("html");
    expect(error).not.toHaveTextContent("Gateway");
    expect(screen.queryByTestId("tg-composer-retry")).toBeNull();
    expect(input.value).toBe("Vielleicht schon raus");
    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(telegramChatKey(1)));
    expect(keys).toContain(JSON.stringify(["tickets", 1, "articles"]));
  });

  it("treats a JSON 500 as unclear too", async () => {
    createArticle.mockRejectedValueOnce(new ApiError(500, { detail: "Internal error" }, "/x"));
    const input = await mount();
    type(input, "Hallo");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    expect(await screen.findByTestId("tg-composer-error")).toHaveTextContent(
      i18n.t("ticket.telegram.composer.sendUncertain"),
    );
    expect(screen.queryByTestId("tg-composer-retry")).toBeNull();
  });

  it("shows a readable message for a 422 with a validation list, never [object Object]", async () => {
    createArticle.mockRejectedValueOnce(
      new ApiError(
        422,
        { detail: [{ type: "too_long", loc: ["body", "attachments"], msg: "List should have at most 10 items" }] },
        "/x",
      ),
    );
    const input = await mount();
    type(input, "Hallo");
    fireEvent.click(screen.getByTestId("tg-composer-send"));
    const error = await screen.findByTestId("tg-composer-error");
    expect(error).not.toHaveTextContent("[object Object]");
    expect(error).toHaveTextContent(i18n.t("ticket.telegram.composer.sendError"));
    expect(screen.getByTestId("tg-composer-retry")).toBeInTheDocument();
  });
});

describe("TelegramChatComposer: attachment count", () => {
  it("refuses an 11th attachment with its own message", async () => {
    await mount();
    const fileInput = screen.getByTestId("tg-composer-file");
    const ten = Array.from({ length: 10 }, (_, i) => new File([String(i)], `f${i}.txt`, { type: "text/plain" }));
    fireEvent.change(fileInput, { target: { files: ten } });
    await screen.findByTestId("tg-composer-attachment-9");
    expect(screen.queryByTestId("tg-composer-attach-error")).toBeNull();

    fireEvent.change(fileInput, { target: { files: [new File(["x"], "elf.txt", { type: "text/plain" })] } });
    expect(await screen.findByTestId("tg-composer-attach-error")).toHaveTextContent(
      i18n.t("ticket.telegram.composer.attachTooMany", { max: 10 }),
    );
    expect(screen.queryByTestId("tg-composer-attachment-10")).toBeNull();
  });
});
