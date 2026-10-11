import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { ChatAvailabilityForm } from "./ChatAvailabilityForm";
import { agentState, renderWithProviders, routeRequests } from "./chatInboxTestUtils";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { request } };
});

beforeAll(() => i18n.changeLanguage("de"));
afterAll(() => i18n.changeLanguage("en"));
const put = vi.fn();
beforeEach(() => {
  put.mockReset().mockImplementation((b) => ({ ...agentState, ...(b as object) }));
  routeRequests(request, { put });
});
afterEach(() => vi.unstubAllGlobals());

describe("ChatAvailabilityForm", () => {
  it("availability saves", async () => {
    renderWithProviders(<ChatAvailabilityForm />);
    fireEvent.click(await screen.findByTestId("chat-available-switch"));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({ available: true, topics: [], max_concurrent: 3 }),
    );
  });

  it("asks for notification permission once when turned on", async () => {
    const requestPermission = vi.fn().mockResolvedValue("granted");
    vi.stubGlobal("Notification", { permission: "default", requestPermission });
    renderWithProviders(<ChatAvailabilityForm />);
    fireEvent.click(await screen.findByTestId("chat-available-switch"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(requestPermission).toHaveBeenCalledTimes(1);
  });

  it("does not ask when already decided or without the Notification API", async () => {
    const requestPermission = vi.fn();
    vi.stubGlobal("Notification", { permission: "denied", requestPermission });
    renderWithProviders(<ChatAvailabilityForm />);
    fireEvent.click(await screen.findByTestId("chat-available-switch"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it("works in browsers without the Notification API", async () => {
    vi.stubGlobal("Notification", undefined);
    renderWithProviders(<ChatAvailabilityForm />);
    fireEvent.click(await screen.findByTestId("chat-available-switch"));
    await waitFor(() => expect(put).toHaveBeenCalled());
  });

  it("clamps the concurrency to 1-20 and saves on blur", async () => {
    renderWithProviders(<ChatAvailabilityForm />);
    const input = await screen.findByTestId("chat-max-concurrent");
    fireEvent.change(input, { target: { value: "99" } });
    fireEvent.blur(input);
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({ available: false, topics: [], max_concurrent: 20 }),
    );
  });
});
