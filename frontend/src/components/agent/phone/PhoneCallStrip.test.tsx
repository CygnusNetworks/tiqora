import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { PhoneCallStrip } from "./PhoneCallStrip";

function renderStrip(props: Partial<React.ComponentProps<typeof PhoneCallStrip>> = {}) {
  const onDirectionChange = vi.fn();
  const onToggleTimer = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <PhoneCallStrip
        direction="inbound"
        onDirectionChange={onDirectionChange}
        fixed={false}
        number="+49 228 1234"
        elapsed={75}
        running
        onToggleTimer={onToggleTimer}
        hasCustomer
        {...props}
      />
    </I18nextProvider>,
  );
  return { onDirectionChange, onToggleTimer };
}

describe("PhoneCallStrip", () => {
  it("shows the call's direction as a read-only badge when it came from the popup", () => {
    renderStrip({ fixed: true, direction: "outbound", answeredAt: Date.now(), endedAt: Date.now() });
    const badge = screen.getByTestId("new-ticket-direction-badge");
    expect(badge).toHaveTextContent("Outgoing call");
    expect(badge).toHaveAttribute("data-direction", "outbound");
    expect(screen.queryByTestId("new-ticket-direction-in")).not.toBeInTheDocument();
    expect(screen.getByTestId("new-ticket-autoreply-hint")).toHaveTextContent("no acknowledgement");
    expect(screen.getByTestId("phone-call-strip-meta")).toHaveTextContent(/answered .* · ended /);
  });

  it("offers the direction as a toggle when opened by hand, with the effect next to it", () => {
    const { onDirectionChange } = renderStrip();
    expect(screen.queryByTestId("new-ticket-direction-badge")).not.toBeInTheDocument();
    expect(screen.getByTestId("new-ticket-direction-in")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("new-ticket-direction-in")).toHaveTextContent("Call came in");
    expect(screen.getByTestId("new-ticket-direction-out")).toHaveTextContent("I called");
    expect(screen.getByTestId("new-ticket-autoreply-hint")).toHaveTextContent(
      "customer gets the acknowledgement",
    );
    expect(screen.queryByTestId("phone-call-strip-meta")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("new-ticket-direction-out"));
    expect(onDirectionChange).toHaveBeenCalledWith("outbound");
  });

  it("an inbound call without a customer sends no acknowledgement", () => {
    renderStrip({ hasCustomer: false });
    expect(screen.getByTestId("new-ticket-autoreply-hint")).toHaveTextContent(
      "no acknowledgement (no customer)",
    );
  });

  it("carries the number and the call timer with pause", () => {
    const { onToggleTimer } = renderStrip();
    expect(screen.getByTestId("phone-call-strip-number")).toHaveTextContent("+49 228 1234");
    expect(screen.getByTestId("new-ticket-timer")).toHaveTextContent("01:15");
    fireEvent.click(screen.getByTestId("new-ticket-timer-toggle"));
    expect(onToggleTimer).toHaveBeenCalled();
  });
});
