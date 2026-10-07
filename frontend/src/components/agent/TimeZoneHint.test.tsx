import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { browserTimeZone, timeZoneMismatch } from "@/lib/timeZone";
import { TimeZoneHint } from "./TimeZoneHint";

const { authUser, setMyTimeZone } = vi.hoisted(() => ({
  authUser: {
    current: {} as Record<string, unknown>,
  },
  setMyTimeZone: vi.fn(),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: authUser.current }),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { setMyTimeZone },
}));

/** A valid zone guaranteed to differ from the machine running the tests. */
const ELSEWHERE = browserTimeZone() === "Pacific/Kiritimati" ? "Pacific/Pago_Pago" : "Pacific/Kiritimati";

function renderHint() {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <TimeZoneHint />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

describe("timeZoneMismatch", () => {
  it("covers the browser-vs-system and setting-vs-device cases", () => {
    expect(timeZoneMismatch(null, "Europe/Berlin", "America/New_York")).toEqual({
      kind: "browserVsSystem",
      browser: "America/New_York",
      system: "Europe/Berlin",
    });
    expect(timeZoneMismatch(null, "Europe/Berlin", "Europe/Berlin")).toBeNull();
    expect(timeZoneMismatch("Asia/Tokyo", "Europe/Berlin", "Europe/Berlin")).toEqual({
      kind: "settingVsDevice",
      browser: "Europe/Berlin",
      zone: "Asia/Tokyo",
    });
    expect(timeZoneMismatch("Asia/Tokyo", "Europe/Berlin", "Asia/Tokyo")).toBeNull();
    // Missing fields (older backends, test mocks) never show a hint.
    expect(timeZoneMismatch(undefined, undefined, "Asia/Tokyo")).toBeNull();
  });
});

describe("TimeZoneHint", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setMyTimeZone.mockReset();
  });
  afterEach(() => window.localStorage.clear());

  it("renders nothing when the zones agree", () => {
    authUser.current = { id: 1, time_zone: null, default_time_zone: browserTimeZone() };
    renderHint();
    expect(screen.queryByTestId("tz-hint")).toBeNull();
  });

  it("offers the browser zone in one click when mails use another one", async () => {
    authUser.current = { id: 1, time_zone: null, default_time_zone: ELSEWHERE };
    const updated = { id: 1, time_zone: browserTimeZone(), default_time_zone: ELSEWHERE };
    setMyTimeZone.mockResolvedValue(updated);
    const { queryClient } = renderHint();

    expect(screen.getByTestId("tz-hint")).toHaveTextContent(ELSEWHERE);
    fireEvent.click(screen.getByTestId("tz-hint-use-browser"));
    await vi.waitFor(() => expect(setMyTimeZone).toHaveBeenCalledWith(browserTimeZone()));
    await vi.waitFor(() => expect(queryClient.getQueryData(["auth", "me"])).toEqual(updated));
  });

  it("tells a travelling agent which zone times are shown in", () => {
    authUser.current = { id: 1, time_zone: ELSEWHERE, default_time_zone: "UTC" };
    renderHint();
    const hint = screen.getByTestId("tz-hint");
    expect(hint).toHaveTextContent(ELSEWHERE);
    expect(hint).toHaveTextContent(browserTimeZone());
    expect(screen.queryByTestId("tz-hint-use-browser")).toBeNull();
    expect(screen.getByTestId("tz-hint-choose")).toBeInTheDocument();
  });

  it("stays dismissed for the same mismatch, and comes back for a new one", () => {
    authUser.current = { id: 1, time_zone: ELSEWHERE, default_time_zone: "UTC" };
    const first = renderHint();
    fireEvent.click(screen.getByTestId("tz-hint-dismiss"));
    expect(screen.queryByTestId("tz-hint")).toBeNull();
    first.unmount();

    const second = renderHint();
    expect(screen.queryByTestId("tz-hint")).toBeNull();
    second.unmount();

    authUser.current = { id: 1, time_zone: null, default_time_zone: ELSEWHERE };
    renderHint();
    expect(screen.getByTestId("tz-hint")).toBeInTheDocument();
  });
});
