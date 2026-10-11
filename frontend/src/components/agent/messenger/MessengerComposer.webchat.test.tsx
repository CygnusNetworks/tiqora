import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { ticketChatKey } from "@/lib/webchatApi";
import { MessengerComposer } from "./MessengerComposer";
import { webchatAdapter } from "./adapters";

const { createArticle, request, listTemplates, acquireTicketLock, getTicket, listReferenceStates, getState, formDrafts } =
  vi.hoisted(() => ({
    createArticle: vi.fn(),
    request: vi.fn(),
    listTemplates: vi.fn(),
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
    api: { createArticle, request, listTemplates, acquireTicketLock, getTicket, listReferenceStates },
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

let qc: QueryClient;

// The strings under test are the German ones.
beforeAll(() => i18n.changeLanguage("de"));
afterAll(() => i18n.changeLanguage("en"));

beforeEach(() => {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  createArticle.mockReset().mockResolvedValue({ article_id: 99 });
  request.mockReset().mockImplementation((method: string, path: string) =>
    Promise.resolve(
      method === "GET" && path.endsWith("/chat")
        ? { session_id: "s1", status: "active", visitor_name: "Max Beispiel" }
        : {},
    ),
  );
  listTemplates.mockReset().mockResolvedValue([]);
  acquireTicketLock.mockReset().mockResolvedValue({ result: "acquired" });
  getTicket.mockReset().mockResolvedValue({ id: 116, permissions: { rw: true, note: true } });
  listReferenceStates.mockReset().mockResolvedValue([{ id: 4, name: "open", type_name: "open" }]);
  getState.mockReset().mockResolvedValue({ drafts: [] });
  formDrafts.list.mockReset().mockResolvedValue([]);
  formDrafts.upsert.mockReset().mockResolvedValue({});
  formDrafts.remove.mockReset().mockResolvedValue(undefined);
});

afterEach(() => vi.useRealTimers());

async function mount() {
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <MessengerComposer ticketId={116} adapter={webchatAdapter} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  const input = (await screen.findByTestId("tg-composer-input")) as HTMLTextAreaElement;
  await waitFor(() => expect(formDrafts.list).toHaveBeenCalled());
  return input;
}
const type = (el: HTMLTextAreaElement, value: string) =>
  fireEvent.change(el, { target: { value, selectionStart: value.length } });

describe("MessengerComposer with the web chat adapter", () => {
  it("sends channel webchat without buttons or quote", async () => {
    const input = await mount();
    type(input, "Hallo");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(createArticle).toHaveBeenCalledTimes(1));
    expect(createArticle).toHaveBeenCalledWith(
      116,
      expect.objectContaining({
        channel: "webchat",
        body: "Hallo",
        sender_type: "agent",
        subject: "",
        is_visible_for_customer: true,
      }),
    );
    const payload = createArticle.mock.calls[0][1];
    expect(payload).not.toHaveProperty("telegram_buttons");
    expect(payload).not.toHaveProperty("telegram_reply_to_article_id");
    expect(screen.queryByTestId("tg-composer-buttons-toggle")).toBeNull();
    expect(screen.queryByTitle(/Buttons unter der Nachricht/)).toBeNull();
  });

  it("names the visitor in the placeholder and counts to 4000", async () => {
    const input = await mount();
    await waitFor(() => expect(input.placeholder).toContain("Max Beispiel"));
    type(input, "a".repeat(4001));
    expect(screen.getByTestId("tg-composer-counter").textContent).toBe("4001 / 4000");
    expect(screen.getByTestId("tg-composer-send")).toBeDisabled();
  });

  it("typing pings go to the webchat endpoint at most every 4 s", async () => {
    const input = await mount();
    await waitFor(() => expect(input.placeholder).toContain("Max Beispiel"));
    const typing = request.mock.calls;
    const pings = () => typing.filter((c) => c[1] === "/api/v1/tickets/116/chat/typing").length;
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-10T10:00:00Z"));
    type(input, "H");
    type(input, "Ha");
    type(input, "Hal");
    expect(pings()).toBe(1);
    vi.setSystemTime(new Date("2026-10-10T10:00:04.100Z"));
    type(input, "Hall");
    expect(pings()).toBe(2);
  });

  it("409 chat_not_live shows the ended hint and refreshes the chat", async () => {
    createArticle.mockRejectedValueOnce(new ApiError(409, { detail: "chat_not_live" }, "/x"));
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    const input = await mount();
    type(input, "Hallo");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("Der Chat ist beendet – bitte per E-Mail antworten.")).toBeInTheDocument();
    expect(screen.queryByTestId("tg-composer-retry")).toBeNull();
    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(ticketChatKey(116)));
  });
});
