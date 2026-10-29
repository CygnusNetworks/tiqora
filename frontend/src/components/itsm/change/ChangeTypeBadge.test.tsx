import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { ChangeType } from "@/lib/changeApi";
import { ChangeTypeBadge } from "./ChangeTypeBadge";

beforeEach(async () => {
  await i18n.changeLanguage("de");
});
afterEach(cleanup);

describe("ChangeTypeBadge", () => {
  it.each([
    ["standard", "Standard-Change", "text-muted"],
    ["normal", "Normaler Change", "text-ink"],
    ["emergency", "Notfall-Change", "text-danger"],
  ] as [ChangeType, string, string][])("renders %s as %s", (type, label, cls) => {
    render(
      <I18nextProvider i18n={i18n}>
        <ChangeTypeBadge type={type} />
      </I18nextProvider>,
    );
    const badge = screen.getByTestId("change-type-badge");
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveAttribute("data-change-type", type);
    expect(badge.className).toContain(cls);
  });
});
