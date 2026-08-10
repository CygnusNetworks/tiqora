import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { RequirePortalEnabled } from "./RequirePortalEnabled";

let portalEnabled = true;
let portalLoading = false;

vi.mock("@/lib/usePortalEnabled", () => ({
  usePortalEnabled: () => ({ portalEnabled, isLoading: portalLoading }),
}));

const navigateMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  Navigate: ({ to }: { to: string }) => {
    navigateMock(to);
    return <div data-testid="navigate-stub">{to}</div>;
  },
}));

function renderGate() {
  return render(
    <RequirePortalEnabled>
      <div data-testid="portal-child">portal</div>
    </RequirePortalEnabled>,
  );
}

describe("RequirePortalEnabled", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    portalEnabled = true;
    portalLoading = false;
  });

  it("renders the portal while it is switched on", () => {
    renderGate();
    expect(screen.getByTestId("portal-child")).toBeInTheDocument();
  });

  it("redirects to the agent login while the portal is switched off", () => {
    portalEnabled = false;
    renderGate();
    expect(screen.queryByTestId("portal-child")).toBeNull();
    expect(navigateMock).toHaveBeenCalledWith("/login");
  });

  it("shows a spinner instead of flashing the portal before the state is known", () => {
    portalLoading = true;
    renderGate();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-child")).toBeNull();
  });
});
