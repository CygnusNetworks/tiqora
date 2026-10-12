import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { WebchatSessionHeader } from "./WebchatSessionHeader";
import { safePageLink } from "./pageLink";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { request } };
});

// Route the wrapper's requests to per-endpoint mocks.
const ticketChat = vi.fn();
const accept = vi.fn();
const end = vi.fn();

const base = {
  session_id: "s1",
  status: "waiting",
  visitor_name: "Max Beispiel",
  visitor_email: "max@example.org",
  visitor_email_verified: false,
  topic_label: "Support",
  language: "de",
  page_url: "https://www.example.org/hilfe",
  assigned_user_id: null,
  assigned_name: null,
  waiting_since: new Date(Date.now() - 125_000).toISOString(),
  accepted_at: null,
  ended_at: null,
  end_reason: null,
  visitor_online: true,
  visitor_typing: false,
};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <WebchatSessionHeader ticketId={116} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

// The strings under test are the German ones.
beforeAll(() => i18n.changeLanguage("de"));
afterAll(() => i18n.changeLanguage("en"));

beforeEach(() => {
  request.mockReset().mockImplementation((method: string, path: string, opts?: { body?: unknown }) => {
    if (path.endsWith("/accept")) return accept(path.split("/")[5]);
    if (path.endsWith("/end")) return end(path.split("/")[5], (opts?.body as { close_ticket: boolean }).close_ticket);
    if (method === "GET" && path.endsWith("/chat")) return ticketChat();
    return Promise.resolve({});
  });
  ticketChat.mockReset().mockResolvedValue(base);
  accept.mockReset().mockResolvedValue({ ticket_id: 116 });
  end.mockReset().mockResolvedValue(undefined);
});

describe("WebchatSessionHeader", () => {
  it("shows visitor, e-mail (unverified), topic, link and the waiting time", async () => {
    mount();
    expect(await screen.findByTestId("webchat-visitor-name")).toHaveTextContent("Max Beispiel");
    expect(screen.getByTestId("webchat-visitor-email")).toHaveTextContent("max@example.org");
    expect(screen.getByTestId("webchat-email-unverified")).toHaveTextContent("E-Mail nicht bestätigt");
    expect(screen.getByTestId("webchat-topic")).toHaveTextContent("Support");
    const link = screen.getByTestId("webchat-page-link");
    expect(link).toHaveAttribute("href", "https://www.example.org/hilfe");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent("www.example.org/hilfe");
    expect(screen.getByTestId("webchat-status")).toHaveTextContent("Wartet seit 2 Min.");
  });

  it("falls back to 'Besucher' and shows typing", async () => {
    ticketChat.mockResolvedValue({ ...base, visitor_name: null, visitor_typing: true });
    mount();
    expect(await screen.findByTestId("webchat-visitor-name")).toHaveTextContent("Besucher");
    expect(screen.getByTestId("webchat-visitor-typing")).toHaveTextContent("schreibt …");
  });

  it("accept calls the API; a 409 already_taken shows the hint", async () => {
    accept.mockRejectedValueOnce(new ApiError(409, { detail: "already_taken" }, "/x"));
    mount();
    fireEvent.click(await screen.findByTestId("webchat-accept"));
    await waitFor(() => expect(accept).toHaveBeenCalledWith("s1"));
    expect(await screen.findByText("Schon angenommen")).toBeInTheDocument();
  });

  it("accept refused for capacity says so", async () => {
    accept.mockRejectedValueOnce(new ApiError(409, { detail: "at_capacity" }, "/x"));
    mount();
    fireEvent.click(await screen.findByTestId("webchat-accept"));
    expect(await screen.findByText(/maximale Zahl gleichzeitiger Chats/)).toBeInTheDocument();
  });

  it("explains a refused accept (403 and not_waiting)", async () => {
    accept.mockRejectedValueOnce(new ApiError(403, { detail: "forbidden" }, "/x"));
    mount();
    fireEvent.click(await screen.findByTestId("webchat-accept"));
    expect(await screen.findByText("Dazu haben Sie keine Berechtigung.")).toBeInTheDocument();
  });

  it("the online dot has an accessible name", async () => {
    mount();
    expect(await screen.findByRole("img", { name: "Besucher online" })).toBeInTheDocument();
  });

  it("an active chat can be ended and the ticket closed", async () => {
    ticketChat.mockResolvedValue({ ...base, status: "active", assigned_name: "Alex Turner", waiting_since: null });
    mount();
    expect(await screen.findByTestId("webchat-status")).toHaveTextContent("Im Chat mit Alex Turner");
    expect(screen.queryByTestId("webchat-accept")).toBeNull();
    fireEvent.click(screen.getByTestId("webchat-end"));
    fireEvent.click(await screen.findByTestId("webchat-end-close"));
    await waitFor(() => expect(end).toHaveBeenCalledWith("s1", true));
  });

  it("an ended chat has no buttons", async () => {
    ticketChat.mockResolvedValue({ ...base, status: "ended" });
    mount();
    expect(await screen.findByTestId("webchat-status")).toHaveTextContent("Beendet");
    expect(screen.queryByTestId("webchat-accept")).toBeNull();
    expect(screen.queryByTestId("webchat-end")).toBeNull();
  });

  it("renders a javascript: page URL as text, never as a link", async () => {
    ticketChat.mockResolvedValue({ ...base, page_url: "javascript:alert(1)" });
    mount();
    expect(await screen.findByTestId("webchat-page-text")).toHaveTextContent("javascript:alert(1)");
    expect(screen.queryByTestId("webchat-page-link")).toBeNull();
    expect(document.querySelector("a")).toBeNull();
  });

  it("safePageLink accepts only http(s) and shortens long paths", () => {
    expect(safePageLink("data:text/html,x")).toBeNull();
    expect(safePageLink("not a url")).toBeNull();
    const l = safePageLink("https://example.org/" + "a".repeat(100));
    expect(l?.label.length).toBe(60);
  });

  it("renders nothing without a chat", async () => {
    ticketChat.mockRejectedValue(new ApiError(404, { detail: "x" }, "/x"));
    mount();
    await waitFor(() => expect(ticketChat).toHaveBeenCalled());
    expect(screen.queryByTestId("webchat-session-header")).toBeNull();
  });
  it("bot status: shows the AI chip and takes over via accept", async () => {
    ticketChat.mockResolvedValue({ ...base, status: "bot", waiting_since: null, bot_name: "Mia" });
    mount();
    expect(await screen.findByTestId("webchat-status")).toHaveTextContent("Mit KI im Gespräch (Mia)");
    expect(screen.queryByTestId("webchat-accept")).toBeNull();
    fireEvent.click(screen.getByTestId("webchat-takeover"));
    expect(screen.getByTestId("webchat-takeover")).toHaveTextContent("Übernehmen");
    await waitFor(() => expect(accept).toHaveBeenCalledWith("s1"));
  });

  it.each([
    ["visitor", "Besucher wollte einen Menschen"],
    ["ai", "Die KI hat den Chat abgegeben"],
    ["limit", "Limit der KI erreicht"],
    ["error", "KI nicht erreichbar"],
    ["unavailable", "KI nicht verfügbar"],
    ["takeover", "Von einem Agenten übernommen"],
  ])("handoff reason %s is spelled out", async (reason, text) => {
    ticketChat.mockResolvedValue({ ...base, status: "waiting", handoff_reason: reason });
    mount();
    expect(await screen.findByTestId("webchat-handoff")).toHaveTextContent(text);
  });

  it("an unknown handoff reason renders no handoff line", async () => {
    ticketChat.mockResolvedValue({ ...base, handoff_reason: "something_new" });
    mount();
    await screen.findByTestId("webchat-visitor-name");
    expect(screen.queryByTestId("webchat-handoff")).toBeNull();
  });

  it("website identity: confirmed chip instead of the unverified one", async () => {
    ticketChat.mockResolvedValue({ ...base, identity_source: "website", visitor_email_verified: true });
    mount();
    expect(await screen.findByTestId("webchat-identity-confirmed")).toHaveTextContent(
      "Identität bestätigt · Website-Anmeldung",
    );
    expect(screen.queryByTestId("webchat-email-unverified")).toBeNull();
  });

  it("portal identity names the customer portal; phase 1 stays unverified", async () => {
    ticketChat.mockResolvedValue({ ...base, identity_source: "portal", visitor_email_verified: true });
    mount();
    expect(await screen.findByTestId("webchat-identity-confirmed")).toHaveTextContent("Kundenportal");
  });
});
