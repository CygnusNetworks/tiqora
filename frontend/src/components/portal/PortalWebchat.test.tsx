import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PortalWebchat } from "./PortalWebchat";

let customer: { login: string } | null = { login: "erika" };
vi.mock("@/auth/CustomerAuthContext", () => ({
  useCustomerAuth: () => ({ customer }),
}));

const request = vi.fn();
vi.mock("@/lib/portalApi", async () => {
  const actual = await vi.importActual<typeof import("@tiqora/api-client")>("@tiqora/api-client");
  return { portalApi: { request: (...a: unknown[]) => request(...a) }, ApiError: actual.ApiError };
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = () => (
    <QueryClientProvider client={qc}>
      <PortalWebchat />
    </QueryClientProvider>
  );
  const r = render(ui());
  return { ...r, again: () => r.rerender(ui()) };
}
const scripts = () => document.querySelectorAll("script[data-tiqora-chat]");

describe("PortalWebchat", () => {
  let logout: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    customer = { login: "erika" };
    request.mockReset();
    scripts().forEach((s) => s.remove());
    logout = vi.fn();
    (window as unknown as { Tiqora?: unknown }).Tiqora = { chat: { logout }, other: { keep: true } };
  });
  afterEach(() => {
    delete (window as unknown as { Tiqora?: unknown }).Tiqora;
    scripts().forEach((s) => s.remove());
  });

  it("appends exactly one loader script with the public key", async () => {
    request.mockResolvedValue({ public_key: "wc_portal" });
    const { again, unmount } = mount();
    await waitFor(() => expect(scripts()).toHaveLength(1));
    const s = scripts()[0] as HTMLScriptElement;
    expect(s.getAttribute("src")).toBe("/chat/v1/loader.js");
    expect(s.getAttribute("data-tiqora-chat")).toBe("wc_portal");
    expect(request).toHaveBeenCalledWith("GET", "/api/portal/webchat", expect.anything());
    again();
    expect(scripts()).toHaveLength(1);
    unmount();
    // a second mount (another portal page after a login) does not add a second loader
    mount();
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(scripts()).toHaveLength(1);
  });

  it("adds nothing when the portal has no chat (404)", async () => {
    const { ApiError } = await import("@tiqora/api-client");
    request.mockRejectedValue(new ApiError(404, { detail: "not_found" }, "/api/portal/webchat"));
    mount();
    await waitFor(() => expect(request).toHaveBeenCalled());
    await act(async () => {});
    expect(scripts()).toHaveLength(0);
  });

  it("does not ask the server while nobody is signed in", async () => {
    customer = null;
    mount();
    await act(async () => {});
    expect(request).not.toHaveBeenCalled();
    expect(scripts()).toHaveLength(0);
    expect(logout).not.toHaveBeenCalled();
  });

  it("calls the widget logout once when the customer logs out", async () => {
    request.mockResolvedValue({ public_key: "wc_portal" });
    const { again } = mount();
    await waitFor(() => expect(scripts()).toHaveLength(1));
    expect(logout).not.toHaveBeenCalled();
    customer = null;
    again();
    expect(logout).toHaveBeenCalledTimes(1);
    again();
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("calls the widget logout once when the portal shell is torn down by the logout", async () => {
    request.mockResolvedValue({ public_key: "wc_portal" });
    const { unmount } = mount();
    await waitFor(() => expect(scripts()).toHaveLength(1));
    unmount();
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("leaves the rest of window.Tiqora alone and survives a missing widget", async () => {
    request.mockResolvedValue({ public_key: "wc_portal" });
    const { unmount } = mount();
    await waitFor(() => expect(scripts()).toHaveLength(1));
    unmount();
    expect((window as unknown as { Tiqora: { other: unknown } }).Tiqora.other).toEqual({ keep: true });
    expect(scripts()).toHaveLength(1);

    delete (window as unknown as { Tiqora?: unknown }).Tiqora;
    const second = mount();
    expect(() => second.unmount()).not.toThrow();
  });
});
