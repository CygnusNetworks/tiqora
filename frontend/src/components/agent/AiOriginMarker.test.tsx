import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AiOriginMarker, AiOriginToggle } from "./AiOriginBadge";

/**
 * The marker exists so an agent scanning the article list can tell an
 * auto-sent reply from one a colleague wrote, without opening anything.
 */
describe("AiOriginMarker", () => {
  it("is not a button — a list row is itself clickable", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <AiOriginMarker articleId={42} />
      </I18nextProvider>,
    );
    const marker = screen.getByTestId("ai-origin-marker-42");
    expect(marker.tagName).toBe("SPAN");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says it was auto-sent without promising a tool trace it cannot open", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <AiOriginMarker articleId={42} />
      </I18nextProvider>,
    );
    const label = screen.getByTestId("ai-origin-marker-42").getAttribute("aria-label") ?? "";
    expect(label).toMatch(/auto-sent/i);
    expect(label).not.toMatch(/click/i);
  });

  it("stays distinct from the toggle, which does open the trace", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <AiOriginToggle articleId={42} open={false} onToggle={() => {}} />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("ai-origin-badge-42").tagName).toBe("BUTTON");
  });
});
