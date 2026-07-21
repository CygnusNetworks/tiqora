import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Avatar } from "./Avatar";

describe("Avatar", () => {
  it("prefers an explicit avatarUrl over Gravatar", () => {
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
  });

  it("uses Gravatar when no avatarUrl is provided", () => {
    render(<Avatar email="jane@example.com" initials="JD" testId="av" />);
    const img = screen.getByTestId("av");
    expect(img).toHaveAttribute(
      "src",
      expect.stringMatching(/^https:\/\/www\.gravatar\.com\/avatar\/[0-9a-f]{32}\?/),
    );
  });

  it("falls back to initials when the image fails to load", () => {
    render(
      <Avatar avatarUrl="https://example.com/missing.png" initials="JD" testId="av" />,
    );
    fireEvent.error(screen.getByTestId("av"));
    expect(screen.getByTestId("av")).toHaveTextContent("JD");
  });

  it("shows initials when neither avatarUrl nor email is set", () => {
    render(<Avatar initials="AB" testId="av" />);
    expect(screen.getByTestId("av")).toHaveTextContent("AB");
  });
});
