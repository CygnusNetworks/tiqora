import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { sha256 } from "@/lib/sha256";
import { Avatar } from "./Avatar";

const JANE = `/api/v1/avatars/email/${sha256("jane@example.com")}`;

describe("Avatar", () => {
  it("prefers an explicit avatarUrl over the proxy", () => {
    render(
      <Avatar
        avatarUrl="https://lh3.googleusercontent.com/a/photo"
        email="jane@example.com"
        initials="JD"
        testId="av"
      />,
    );
    const img = screen.getByTestId("av");
    expect(img.tagName).toBe("IMG");
    expect(img).toHaveAttribute("src", "https://lh3.googleusercontent.com/a/photo");
    expect(img).toHaveAttribute("data-kind", "explicit");
  });

  it("asks the avatar proxy by e-mail hash when no avatarUrl is provided", () => {
    render(<Avatar email="Jane@Example.com" initials="JD" testId="av" />);
    const img = screen.getByTestId("av");
    expect(img).toHaveAttribute("src", JANE);
    expect(img).toHaveAttribute("data-kind", "person");
    expect(img).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it("walks explicit → person → domain logo → initials on load errors", () => {
    render(
      <Avatar
        avatarUrl="https://example.com/missing.png"
        email="jane@example.com"
        initials="JD"
        testId="av"
      />,
    );
    fireEvent.error(screen.getByTestId("av"));
    expect(screen.getByTestId("av")).toHaveAttribute("src", JANE);

    fireEvent.error(screen.getByTestId("av"));
    const logo = screen.getByTestId("av");
    expect(logo).toHaveAttribute("src", "/api/v1/avatars/domain/example.com");
    expect(logo).toHaveAttribute("data-kind", "domain");
    expect(logo.className).toContain("object-contain");
    expect(logo.className).toContain("bg-white");

    fireEvent.error(screen.getByTestId("av"));
    expect(screen.getByTestId("av").tagName).toBe("SPAN");
    expect(screen.getByTestId("av")).toHaveTextContent("JD");
  });

  it("skips the domain logo when domainFallback is off", () => {
    render(<Avatar email="jane@example.com" domainFallback={false} initials="JD" testId="av" />);
    fireEvent.error(screen.getByTestId("av"));
    expect(screen.getByTestId("av")).toHaveTextContent("JD");
  });

  it("shows only the logo for an explicit domain, with the version as cache buster", () => {
    render(<Avatar domain="example.org" version="42" initials="EO" testId="av" />);
    expect(screen.getByTestId("av")).toHaveAttribute(
      "src",
      "/api/v1/avatars/domain/example.org?v=42",
    );
  });

  it("appends the version to the person picture", () => {
    render(<Avatar email="jane@example.com" version="7" initials="JD" testId="av" />);
    expect(screen.getByTestId("av")).toHaveAttribute("src", `${JANE}?v=7`);
  });

  it("shows initials when there is no source at all", () => {
    render(<Avatar initials="AB" testId="av" />);
    expect(screen.getByTestId("av")).toHaveTextContent("AB");
  });

  it("does not try a domain logo for an address without a domain", () => {
    render(<Avatar email="jane" initials="J" testId="av" />);
    fireEvent.error(screen.getByTestId("av"));
    expect(screen.getByTestId("av")).toHaveTextContent("J");
  });
});
