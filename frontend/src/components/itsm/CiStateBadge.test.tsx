import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ciStateColor } from "@/lib/ciDisplay";
import { CiStateBadge } from "./CiStateBadge";

afterEach(cleanup);

describe("ciStateColor", () => {
  it("maps operational to green, warning to warn, incident to danger", () => {
    expect(ciStateColor("operational")).toBe("green");
    expect(ciStateColor("warning")).toBe("warn");
    expect(ciStateColor("incident")).toBe("danger");
  });

  it("falls back to muted for unknown or missing functionality", () => {
    expect(ciStateColor("productive")).toBe("muted");
    expect(ciStateColor(null)).toBe("muted");
    expect(ciStateColor(undefined)).toBe("muted");
  });
});

describe("CiStateBadge", () => {
  it.each([
    ["operational", "Betriebsbereit", "green", "text-green"],
    ["warning", "Eingeschränkt", "warn", "text-escalation"],
    ["incident", "Störung", "danger", "text-danger"],
  ])("renders %s with the %s token", (functionality, name, color, cls) => {
    render(<CiStateBadge state={{ key: "1", name, functionality }} />);
    const badge = screen.getByTestId("ci-state-badge");
    expect(badge).toHaveTextContent(name);
    expect(badge).toHaveAttribute("data-state-color", color);
    expect(badge.className).toContain(cls);
  });

  it("shows a dash without a state", () => {
    render(<CiStateBadge state={null} />);
    expect(screen.queryByTestId("ci-state-badge")).toBeNull();
    expect(screen.getByText("–")).toBeInTheDocument();
  });
});
