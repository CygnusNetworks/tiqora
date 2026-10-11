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

  it("shows a validation message instead of saving empty or out-of-range input", async () => {
    renderWithProviders(<ChatAvailabilityForm />);
    const input = await screen.findByTestId("chat-max-concurrent");
    fireEvent.change(input, { target: { value: "99" } });
    fireEvent.blur(input);
    expect(await screen.findByTestId("chat-max-invalid")).toHaveTextContent("von 1 bis 20");
    expect(input).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    expect(await screen.findByTestId("chat-max-invalid")).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();
  });

  it("saves the limit on blur", async () => {
    renderWithProviders(<ChatAvailabilityForm />);
    const input = await screen.findByTestId("chat-max-concurrent");
    fireEvent.change(input, { target: { value: "7" } });
    fireEvent.blur(input);
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({ available: false, topics: [], max_concurrent: 7 }),
    );
  });

  it("Enter followed by blur sends one PUT", async () => {
    renderWithProviders(<ChatAvailabilityForm />);
    const input = await screen.findByTestId("chat-max-concurrent");
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  });

  it("a failed save resets the field to the server value and shows the error", async () => {
    put.mockRejectedValueOnce(new Error("boom"));
    renderWithProviders(<ChatAvailabilityForm />);
    const input = await screen.findByTestId("chat-max-concurrent");
    fireEvent.change(input, { target: { value: "7" } });
    fireEvent.blur(input);
    expect(await screen.findByTestId("chat-availability-error")).toBeInTheDocument();
    await waitFor(() => expect(input).toHaveValue(3));
  });

  it("editing the limit then clicking the switch keeps both changes", async () => {
    let release: () => void = () => undefined;
    put.mockImplementationOnce(
      (b) => new Promise((res) => { release = () => res({ ...agentState, ...(b as object) }); }),
    );
    renderWithProviders(<ChatAvailabilityForm />);
    const input = await screen.findByTestId("chat-max-concurrent");
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.blur(input);
    fireEvent.click(screen.getByTestId("chat-available-switch"));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    release();
    await waitFor(() =>
      expect(put).toHaveBeenLastCalledWith({ available: true, topics: [], max_concurrent: 5 }),
    );
    expect(put).toHaveBeenCalledTimes(2);
  });
});
