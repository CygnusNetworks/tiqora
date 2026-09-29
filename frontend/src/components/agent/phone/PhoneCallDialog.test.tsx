import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError, type TicketDetail } from "@/lib/api";
import { loadPhoneDraft, savePhoneDraft } from "@/lib/phoneCall";
import { PhoneCallDialog } from "./PhoneCallDialog";

const { listReferenceStates, logPhoneCall, screenDynamicFields, refineAvailability } = vi.hoisted(
  () => ({
    listReferenceStates: vi.fn(),
    logPhoneCall: vi.fn(),
    screenDynamicFields: vi.fn(),
    refineAvailability: vi.fn(),
  }),
);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { listReferenceStates } };
});

vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return { ...actual, phoneApi: { logPhoneCall, screenDynamicFields } };
});

vi.mock("@/lib/refineApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/refineApi")>("@/lib/refineApi");
  return { ...actual, refineApi: { refine: vi.fn(), refineAvailability } };
});

const STATES = [
  { id: 4, name: "open", type_name: "open" },
  { id: 2, name: "closed successful", type_name: "closed" },
  { id: 8, name: "pending reminder", type_name: "pending reminder" },
];

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: 7,
    tn: "20240601000001",
    title: "Drucker",
    queue_id: 1,
    state_id: 4,
    state: "open",
    customer_id: "C-9",
    customer_user_id: "bob",
    permissions: { ro: true, move_into: true, create: true, note: true, owner: true, priority: true, rw: true },
    dynamic_fields: [{ name: "Topic", label: "Topic", values: ["alt"] }],
    ...overrides,
  } as TicketDetail;
}

function renderDialog(props: Partial<React.ComponentProps<typeof PhoneCallDialog>> = {}) {
  const onClose = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PhoneCallDialog ticket={makeTicket()} initialDirection="inbound" onClose={onClose} {...props} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { ...utils, onClose };
}

beforeEach(() => {
  window.localStorage.clear();
  listReferenceStates.mockReset().mockResolvedValue(STATES);
  logPhoneCall.mockReset().mockResolvedValue({ article_id: 1, ticket_id: 7, time_accounting_id: 3, locked: false });
  screenDynamicFields.mockReset().mockResolvedValue([]);
  refineAvailability.mockReset().mockResolvedValue({ available: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("PhoneCallDialog", () => {
  it("runs the timer, prefills subject and books the call's minutes with the inbound default", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    renderDialog();
    expect(screen.getByTestId("phone-subject")).toHaveValue(i18n.t("phone.subjectInbound", { name: "bob" }));
    await screen.findByTestId("phone-next-default");
    expect(screen.getByTestId("phone-next-default")).toHaveAttribute("aria-pressed", "true");

    act(() => {
      vi.advanceTimersByTime(65_000);
    });
    expect(screen.getByTestId("phone-timer")).toHaveTextContent("01:05");
    expect(screen.getByTestId("phone-time")).toHaveValue(2);

    fireEvent.change(screen.getByTestId("phone-body"), { target: { value: "Drucker defekt" } });
    fireEvent.click(screen.getByTestId("phone-save"));
    await waitFor(() => expect(logPhoneCall).toHaveBeenCalled());
    const [ticketId, body] = logPhoneCall.mock.calls[0];
    expect(ticketId).toBe(7);
    expect(body).toMatchObject({
      direction: "inbound",
      body: "Drucker defekt",
      time_unit: 2,
      state_id: 4,
      is_visible_for_customer: true,
    });
    expect(body.pending_time).toBeUndefined();
  });

  it("pauses the timer and lets a typed time override the timer", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    renderDialog();
    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    fireEvent.click(screen.getByTestId("phone-timer-toggle"));
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByTestId("phone-timer")).toHaveTextContent("00:30");

    fireEvent.change(screen.getByTestId("phone-time"), { target: { value: "15" } });
    await screen.findByTestId("phone-next-keep");
    fireEvent.click(screen.getByTestId("phone-next-keep"));
    fireEvent.click(screen.getByTestId("phone-save"));
    await waitFor(() => expect(logPhoneCall).toHaveBeenCalled());
    expect(logPhoneCall.mock.calls[0][1]).toMatchObject({ time_unit: 15 });
    expect(logPhoneCall.mock.calls[0][1].state_id).toBeUndefined();
  });

  it("outbound defaults to closed successful; a callback needs a time and sets the reminder", async () => {
    renderDialog({ initialDirection: "outbound", callerNumber: "+49 228 1" });
    expect(screen.getByTestId("phone-subject")).toHaveValue(i18n.t("phone.subjectOutbound", { name: "bob" }));
    const closeSeg = await screen.findByTestId("phone-next-default");
    expect(closeSeg).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("phone-next-callback"));
    expect(screen.getByTestId("phone-save")).toBeDisabled();
    fireEvent.click(screen.getByTestId("phone-callback-preset-tomorrow9"));
    expect(screen.getByTestId("phone-save")).toBeEnabled();
    fireEvent.click(screen.getByTestId("phone-save"));
    await waitFor(() => expect(logPhoneCall).toHaveBeenCalled());
    const body = logPhoneCall.mock.calls[0][1];
    expect(body.direction).toBe("outbound");
    expect(body.state_id).toBe(8);
    expect(body.caller_number).toBe("+49 228 1");
    const pending = new Date(body.pending_time);
    expect(pending.getHours()).toBe(9);
    expect(pending.getMinutes()).toBe(0);
  });

  it("switching direction updates an untouched subject and the default state", async () => {
    renderDialog();
    await screen.findByTestId("phone-next-default");
    fireEvent.click(screen.getByTestId("phone-direction-outbound"));
    expect(screen.getByTestId("phone-subject")).toHaveValue(i18n.t("phone.subjectOutbound", { name: "bob" }));
    fireEvent.click(screen.getByTestId("phone-save"));
    await waitFor(() => expect(logPhoneCall).toHaveBeenCalled());
    expect(logPhoneCall.mock.calls[0][1]).toMatchObject({ direction: "outbound", state_id: 2 });
  });

  it("keeps a draft per ticket and restores it, clearing it after saving", async () => {
    const first = renderDialog();
    fireEvent.change(screen.getByTestId("phone-body"), { target: { value: "halb fertig" } });
    fireEvent.click(screen.getByTestId("phone-direction-outbound"));
    await waitFor(() => expect(loadPhoneDraft(7)?.body).toBe("halb fertig"));
    expect(loadPhoneDraft(7)?.direction).toBe("outbound");
    first.unmount();

    renderDialog();
    expect(screen.getByTestId("phone-body")).toHaveValue("halb fertig");
    expect(screen.getByTestId("phone-direction-outbound")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("phone-discard")).toBeInTheDocument();
    await screen.findByTestId("phone-next-default");
    fireEvent.click(screen.getByTestId("phone-save"));
    await waitFor(() => expect(logPhoneCall).toHaveBeenCalled());
    await waitFor(() => expect(loadPhoneDraft(7)).toBeNull());
  });

  it("resumes the timer from a stored draft", () => {
    savePhoneDraft(7, { direction: "inbound", subject: "", body: "x", elapsed: 125 });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    renderDialog();
    expect(screen.getByTestId("phone-timer")).toHaveTextContent("02:05");
  });

  it("counts from the answered time of a CTI call and wins over a draft", () => {
    savePhoneDraft(7, { direction: "inbound", subject: "", body: "x", elapsed: 5 });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date("2026-09-29T10:02:00Z"));
    renderDialog({ startedAt: Date.parse("2026-09-29T10:00:30Z") });
    expect(screen.getByTestId("phone-timer")).toHaveTextContent("01:30");
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(screen.getByTestId("phone-timer")).toHaveTextContent("01:40");
  });

  it("shows the fixed duration of an ended CTI call", () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date("2026-09-29T10:10:00Z"));
    renderDialog({
      startedAt: Date.parse("2026-09-29T10:00:00Z"),
      endedAt: Date.parse("2026-09-29T10:03:20Z"),
    });
    expect(screen.getByTestId("phone-timer")).toHaveTextContent("03:20");
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.getByTestId("phone-timer")).toHaveTextContent("03:20");
  });

  it("names the agent holding the lock on a 409", async () => {
    logPhoneCall.mockRejectedValue(
      new ApiError(409, { detail: { message: "locked", locked_by_id: 3, locked_by_name: "Otto Other" } }, "/x"),
    );
    renderDialog({ initialDirection: "outbound" });
    await screen.findByTestId("phone-next-default");
    fireEvent.click(screen.getByTestId("phone-save"));
    const msg = await screen.findByTestId("phone-locked");
    expect(msg).toHaveTextContent("Otto Other");
  });

  it("edits dynamic fields and sends only the changed ones; required fields block saving", async () => {
    screenDynamicFields.mockResolvedValue([
      { name: "Topic", label: "Topic", field_type: "Text", required: false, possible_values: null },
      { name: "Area", label: "Area", field_type: "Dropdown", required: true, possible_values: { net: "Netz", tv: "TV" } },
    ]);
    renderDialog();
    const area = await screen.findByTestId("phone-df-Area");
    expect(screen.getByTestId("phone-df-Topic")).toHaveValue("alt");
    expect(screen.getByTestId("phone-save")).toBeDisabled();
    expect(screen.getByTestId("phone-df-missing")).toHaveTextContent("Area");
    fireEvent.change(area, { target: { value: "tv" } });
    fireEvent.click(screen.getByTestId("phone-save"));
    await waitFor(() => expect(logPhoneCall).toHaveBeenCalled());
    expect(logPhoneCall.mock.calls[0][1].dynamic_fields).toEqual({ Area: ["tv"] });
  });
});
