import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { RiskBadge } from "./RiskBadge";

beforeEach(async () => {
  await i18n.changeLanguage("de");
});
afterEach(cleanup);

function renderBadge(level: string | null | undefined) {
  render(
    <I18nextProvider i18n={i18n}>
      <RiskBadge level={level} />
    </I18nextProvider>,
  );
  return screen.getByTestId("risk-badge");
}

describe("RiskBadge", () => {
  it.each([
    ["low", "Niedrig", "text-green"],
    ["medium", "Mittel", "text-ink"],
    ["high", "Hoch", "text-escalation"],
    ["very_high", "Sehr hoch", "text-danger"],
  ])("renders %s as %s", (level, label, cls) => {
    const badge = renderBadge(level);
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveAttribute("data-risk-level", level);
    expect(badge.className).toContain(cls);
  });

  it.each([null, undefined, "", "unknown"])("shows Nicht bewertet for %s", (level) => {
    const badge = renderBadge(level);
    expect(badge).toHaveTextContent("Nicht bewertet");
    expect(badge).toHaveAttribute("data-risk-level", "none");
  });
});
