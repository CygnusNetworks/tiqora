import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CiStateBadge } from "./CiStateBadge";

describe("CiStateBadge", () => {
  it("maps incident functionality onto neutral, warning and danger tokens", () => {
    const { rerender } = render(<CiStateBadge kind="incident" functionality="operational" label="Betriebsbereit" />);
    expect(screen.getByTestId("ci-incident-badge").className).toContain("text-muted");
    rerender(<CiStateBadge kind="incident" functionality="warning" label="Warnung" />);
    expect(screen.getByTestId("ci-incident-badge").className).toContain("text-escalation");
    rerender(<CiStateBadge kind="incident" functionality="incident" label="Vorfall" />);
    expect(screen.getByTestId("ci-incident-badge").className).toContain("text-danger");
    expect(screen.getByText("Vorfall")).toBeInTheDocument();
  });

  it("keeps deployment states neutral", () => {
    render(<CiStateBadge kind="deployment" functionality="productive" label="Produktiv" />);
    expect(screen.getByTestId("ci-deployment-badge").className).toContain("text-ink");
  });
});
