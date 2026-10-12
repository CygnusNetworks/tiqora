import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { useState } from "react";
import i18n from "@/i18n";
import { HoursTab } from "./HoursTab";
import { newDraft, type Draft } from "@/lib/webchatForm";

let lastDraft: Draft = newDraft();
function Harness({ initial, errors = {} }: { initial: Draft; errors?: Record<string, string> }) {
  const [draft, setDraft] = useState(initial);
  lastDraft = draft;
  return (
    <I18nextProvider i18n={i18n}>
      <HoursTab draft={draft} errors={errors} onDraft={setDraft} />
    </I18nextProvider>
  );
}

describe("HoursTab offline mode", () => {
  beforeAll(() => i18n.changeLanguage("de"));
  afterAll(() => i18n.changeLanguage("en"));

  it("disables 'KI antwortet allein' with a hint while no topic answers first", () => {
    render(<Harness initial={newDraft()} />);
    const ai = screen.getByTestId("webchat-offline-mode-ai");
    expect(ai).toHaveTextContent("KI antwortet allein");
    expect(ai).toBeDisabled();
    expect(screen.getByTestId("webchat-offline-ai-hint")).toHaveTextContent("mindestens ein Thema");
    fireEvent.click(ai);
    expect(lastDraft.values.offline_mode).toBe("form");
  });

  it("enables it once a topic answers first and saves the choice", () => {
    const d = newDraft();
    d.values.topics = [{ key: "support", label: "Support", queue_id: 1, priority_id: null, ai_mode: "first_line" }];
    render(<Harness initial={d} />);
    const ai = screen.getByTestId("webchat-offline-mode-ai");
    expect(ai).toBeEnabled();
    expect(screen.queryByTestId("webchat-offline-ai-hint")).toBeNull();
    fireEvent.click(ai);
    expect(lastDraft.values.offline_mode).toBe("ai");
    expect(ai).toHaveAttribute("aria-checked", "true");
  });

  it("shows the server error of offline_mode", () => {
    render(<Harness initial={newDraft()} errors={{ offline_mode: "Mindestens ein Thema muss die KI zuerst antworten lassen." }} />);
    expect(screen.getByTestId("webchat-offline-error")).toHaveTextContent("Mindestens ein Thema");
  });

  it("arrow keys skip the disabled option", () => {
    render(<Harness initial={newDraft()} />);
    fireEvent.keyDown(screen.getByTestId("webchat-offline-mode-form"), { key: "ArrowRight" });
    expect(lastDraft.values.offline_mode).toBe("hide");
  });
});
