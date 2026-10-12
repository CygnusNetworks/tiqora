import { describe, it, expect } from "vitest";
import { buildValues, newDraft, tabOfPath, validateDraft } from "./webchatForm";

describe("webchatForm assistant fields", () => {
  it("routes server paths to the right tab", () => {
    expect(tabOfPath("bot_name")).toBe("ai");
    expect(tabOfPath("bot_tone_prompt")).toBe("ai");
    expect(tabOfPath("topics.2.ai_mode")).toBe("ai");
    expect(tabOfPath("topics.2.label")).toBe("general");
    expect(tabOfPath("show_on_portal")).toBe("embed");
    expect(tabOfPath("identity_create_customers")).toBe("embed");
    expect(tabOfPath("offline_mode")).toBe("hours");
  });

  it("trims the name, drops an empty tone and keeps the new switches in the payload", () => {
    const d = newDraft();
    d.values.bot_name = "  Frieda ";
    d.values.bot_tone_prompt = "   ";
    d.values.show_on_portal = true;
    d.values.identity_create_customers = true;
    d.values.offline_mode = "ai";
    expect(buildValues(d)).toMatchObject({
      bot_name: "Frieda",
      bot_tone_prompt: null,
      show_on_portal: true,
      identity_create_customers: true,
      offline_mode: "ai",
    });
  });

  it("validates the assistant name", () => {
    const d = newDraft();
    d.values.bot_name = "";
    expect(validateDraft(d).bot_name).toBeDefined();
    d.values.bot_name = "a".repeat(80);
    expect(validateDraft(d).bot_name).toBeUndefined();
  });
});
