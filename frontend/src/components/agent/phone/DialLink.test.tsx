import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { phoneApi } from "@/lib/phoneApi";
import { DialLink } from "./DialLink";

/** The phone config is pre-seeded (fresh for 10 min), so the link is in its final mode on first render. */
function renderLink(
  props: React.ComponentProps<typeof DialLink>,
  originate: boolean,
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["reference", "phone-config"], {
    dial_scheme: "tel",
    originate,
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DialLink {...props} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("DialLink click-to-dial", () => {
  it("dials through the PBX when originate is on", async () => {
    const dialSpy = vi
      .spyOn(phoneApi, "dial")
      .mockResolvedValue({ extension: "60", number: "01717630944", ring_timeout: 30 });
    const onDial = vi.fn();
    renderLink(
      {
        number: "+49 171 7630944",
        testId: "d",
        name: "Kettler",
        ticketId: 5,
        onDial,
      },
      true,
    );
    fireEvent.click(screen.getByTestId("d"));
    expect(dialSpy).toHaveBeenCalledWith({
      number: "+49 171 7630944",
      ticket_id: 5,
      name: "Kettler",
    });
    expect(await screen.findByTestId("d-status")).toHaveTextContent("60");
    expect(onDial).toHaveBeenCalled();
  });

  it("keeps the tel: link when originate is off", () => {
    const dialSpy = vi.spyOn(phoneApi, "dial");
    renderLink({ number: "0228123", testId: "d" }, false);
    expect(screen.getByTestId("d")).toHaveAttribute("href", "tel:0228123");
    const link = screen.getByTestId("d");
    link.addEventListener("click", (e) => e.preventDefault()); // jsdom cannot follow tel:
    fireEvent.click(link);
    expect(dialSpy).not.toHaveBeenCalled();
  });

  it("follows the link on ctrl-click even when originate is on", () => {
    const dialSpy = vi.spyOn(phoneApi, "dial");
    renderLink({ number: "0228123", testId: "d" }, true);
    const link = screen.getByTestId("d");
    link.addEventListener("click", (e) => e.preventDefault()); // jsdom cannot follow tel:
    fireEvent.click(link, { ctrlKey: true });
    expect(dialSpy).not.toHaveBeenCalled();
  });

  it("shows the server's reason when dialling fails", async () => {
    vi.spyOn(phoneApi, "dial").mockRejectedValue(
      new ApiError(409, { detail: "no phone extension set for this agent" }, "/api/v1/phone/dial"),
    );
    renderLink({ number: "0228123", testId: "d" }, true);
    fireEvent.click(screen.getByTestId("d"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("no phone extension"),
    );
  });

  it("shows the generic message for a non-API error", async () => {
    vi.spyOn(phoneApi, "dial").mockRejectedValue(new Error("network"));
    renderLink({ number: "0228123", testId: "d" }, true);
    fireEvent.click(screen.getByTestId("d"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("phone.dialFailed")),
    );
  });

  it("runs onDial after a successful dial, not after a failed one", async () => {
    const dialSpy = vi.spyOn(phoneApi, "dial");
    const onDial = vi.fn();
    dialSpy.mockRejectedValueOnce(new ApiError(429, { detail: "slow down" }, "/api/v1/phone/dial"));
    renderLink({ number: "0228123", testId: "d", onDial }, true);
    fireEvent.click(screen.getByTestId("d"));
    await screen.findByRole("alert");
    expect(onDial).not.toHaveBeenCalled();
    dialSpy.mockResolvedValueOnce({ extension: "60", number: "0228123", ring_timeout: 30 });
    fireEvent.click(screen.getByTestId("d"));
    await waitFor(() => expect(onDial).toHaveBeenCalledTimes(1));
  });
});
