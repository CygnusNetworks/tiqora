import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { receiveCallEvent, resetCalls, type CallEventMessage } from "@/lib/callPopup";
import type { ActiveCall } from "@/lib/phoneApi";
import { consumePhoneCallRequest, peekPhoneCallRequest } from "@/lib/phoneCall";
import { CallPopup } from "./CallPopup";

const { navigate, activeCalls, dismissCall, callerLookup } = vi.hoisted(() => ({
  navigate: vi.fn(),
  activeCalls: vi.fn(),
  dismissCall: vi.fn(),
  callerLookup: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 7 } }) }));
vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return { ...actual, phoneApi: { activeCalls, dismissCall, callerLookup } };
});

const T0 = Date.parse("2026-09-29T10:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();

function call(overrides: Partial<ActiveCall> = {}): ActiveCall {
  return {
    call_id: "c1",
    state: "ringing",
    number: "+492285550101",
    extension: "100",
    direction: "inbound",
    user_ids: [7],
    ringing_at: iso(T0),
    answered_at: null,
    ended_at: null,
    ...overrides,
  };
}

function push(event: CallEventMessage["event"], c: ActiveCall) {
  act(() => receiveCallEvent({ type: "call_event", user_ids: c.user_ids, event, call: c }));
}

function renderPopup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CallPopup />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  resetCalls();
  navigate.mockReset();
  activeCalls.mockReset().mockResolvedValue([]);
  dismissCall.mockReset().mockResolvedValue(undefined);
  callerLookup.mockReset().mockResolvedValue({
    number_normalized: "492285550101",
    customers: [
      { login: "bob", customer_id: "C-9", name: "Bob Builder", email: "bob@x.test", phone: "+49 228 5550101", mobile: null, company: "Bau GmbH" },
    ],
    open_tickets: [{ id: 42, tn: "2026092910000042", title: "Drucker", state: "open", queue: "Raw", changed: iso(T0) }],
  });
  void i18n.changeLanguage("en");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("CallPopup", () => {
  it("shows a ringing call with the looked-up customer and open tickets", async () => {
    renderPopup();
    push("ringing", call());
    expect(screen.getByTestId("call-card-c1")).toHaveAttribute("data-state", "ringing");
    expect(screen.getByText("Incoming call")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("call-card-who")).toHaveTextContent("Bob Builder"));
    expect(callerLookup).toHaveBeenCalledWith("+492285550101", expect.anything());
    expect(await screen.findByTestId("call-card-log-42")).toBeInTheDocument();
  });

  it("runs the timer once answered and offers logging after hangup", () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date(T0 + 65_000));
    renderPopup();
    push("answered", call({ state: "answered", answered_at: iso(T0 + 5000) }));
    expect(screen.getByTestId("call-card-timer")).toHaveTextContent("01:00");
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByTestId("call-card-timer")).toHaveTextContent("01:03");
    push("hangup", call({ state: "ended", answered_at: iso(T0 + 5000), ended_at: iso(T0 + 95_000) }));
    expect(screen.getByText("Call ended – log it?")).toBeInTheDocument();
    expect(screen.getByTestId("call-card-timer")).toHaveTextContent("01:30");
  });

  it("drops the card when a colleague answers", () => {
    renderPopup();
    push("ringing", call());
    push("answered", call({ state: "answered", answered_at: iso(T0), user_ids: [8] }));
    expect(screen.queryByTestId("call-card-c1")).not.toBeInTheDocument();
  });

  it("stacks several calls and dismisses one", async () => {
    renderPopup();
    push("ringing", call());
    push("ringing", call({ call_id: "c2", number: "" }));
    expect(screen.getByTestId("call-card-c2")).toHaveTextContent("Unknown number");
    fireEvent.click(screen.getAllByTestId("call-card-dismiss")[0]);
    expect(dismissCall).toHaveBeenCalledTimes(1);
    expect(screen.getAllByTestId(/^call-card-c/)).toHaveLength(1);
  });

  it("logs on an open ticket with the answered time", async () => {
    renderPopup();
    push("answered", call({ state: "answered", answered_at: iso(T0 + 5000) }));
    fireEvent.click(await screen.findByTestId("call-card-log-42"));
    expect(peekPhoneCallRequest(42)).toEqual({
      direction: "inbound",
      number: "+492285550101",
      startedAt: T0 + 5000,
      endedAt: null,
    });
    consumePhoneCallRequest(42);
    expect(dismissCall).toHaveBeenCalledWith("c1");
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/tickets/$ticketId", params: { ticketId: "42" } });
  });

  it("opens a prefilled new phone ticket", async () => {
    const start = Date.now() - 90_000;
    renderPopup();
    push(
      "hangup",
      call({ state: "ended", answered_at: iso(start), ended_at: iso(start + 60_000), answered_by_user_id: 7 }),
    );
    await waitFor(() => expect(screen.getByTestId("call-card-who")).toHaveTextContent("Bob Builder"));
    fireEvent.click(screen.getByTestId("call-card-new-ticket"));
    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/new",
      search: {
        type: "phone",
        from_call: true,
        direction: "inbound",
        number: "+492285550101",
        customer: "bob",
        call_started: start,
        call_ended: start + 60_000,
        owner_id: 7,
      },
    });
  });

  it("leaves the owner open when nobody single answered (shared extension)", async () => {
    renderPopup();
    push("answered", call({ state: "answered", answered_at: iso(Date.now()), answered_by_user_id: null }));
    fireEvent.click(await screen.findByTestId("call-card-new-ticket"));
    expect(navigate.mock.calls.at(-1)?.[0].search.owner_id).toBeUndefined();
  });

  it("restores active calls after a reload", async () => {
    activeCalls.mockResolvedValue([call({ call_id: "restored", state: "ended", answered_at: null, ended_at: new Date().toISOString() })]);
    renderPopup();
    expect(await screen.findByTestId("call-card-restored")).toHaveTextContent("Missed call");
  });
});
