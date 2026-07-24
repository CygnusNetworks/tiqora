import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ConnectionStatus } from "./ConnectionStatus";

const { useConnectionStatus } = vi.hoisted(() => ({
  useConnectionStatus: vi.fn(),
}));

vi.mock("@/lib/useSSE", () => ({
  useConnectionStatus,
}));

function renderStatus() {
  return render(
    <I18nextProvider i18n={i18n}>
      <ConnectionStatus />
    </I18nextProvider>,
  );
}

describe("ConnectionStatus", () => {
  beforeEach(() => {
    useConnectionStatus.mockReset();
    void i18n.changeLanguage("en");
  });

  it("shows the live state with a green dot", () => {
    useConnectionStatus.mockReturnValue("live");
    renderStatus();
    const el = screen.getByTestId("connection-status");
    expect(el).toHaveAttribute("data-state", "live");
    expect(el).toHaveAttribute("title", "Connected live");
    expect(el.querySelector(".text-green")).toBeInTheDocument();
  });

  it("shows the reconnecting state with an amber, pulsing dot", () => {
    useConnectionStatus.mockReturnValue("reconnecting");
    renderStatus();
    const el = screen.getByTestId("connection-status");
    expect(el).toHaveAttribute("data-state", "reconnecting");
    expect(el).toHaveAttribute("title", "Reconnecting…");
    expect(el.querySelector(".text-amber")).toBeInTheDocument();
    expect(el.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("treats the connecting state the same as reconnecting (amber)", () => {
    useConnectionStatus.mockReturnValue("connecting");
    renderStatus();
    const el = screen.getByTestId("connection-status");
    expect(el.querySelector(".text-amber")).toBeInTheDocument();
  });
});
