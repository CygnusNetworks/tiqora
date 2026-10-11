import { describe, expect, it } from "vitest";
import {
  allReadOnlySelection,
  parseScopesToMode,
  selectionToScopes,
} from "./apiKeyScopes";

describe("apiKeyScopes: plugins are an explicit opt-in", () => {
  it("read-only preset contains every area except plugins", () => {
    const csv = selectionToScopes("read_only", allReadOnlySelection());
    expect(csv).toContain("kb:ro");
    expect(csv).not.toContain("plugins");
    expect(allReadOnlySelection().plugins).toBe("off");
  });

  it("legacy read key is shown without plugins", () => {
    const { mode, selection } = parseScopesToMode("read");
    expect(mode).toBe("read_only");
    expect(selection.plugins).toBe("off");
    expect(selection.kb).toBe("ro");
  });

  it("legacy write key is shown without plugins", () => {
    const { mode, selection } = parseScopesToMode("write");
    expect(mode).toBe("custom");
    expect(selection.plugins).toBe("off");
    expect(selection.kb).toBe("rw");
    expect(selection.mcp).toBe("rw");
  });

  it("explicit plugins grant is kept and is not the read-only preset", () => {
    const csv = `${selectionToScopes("read_only", allReadOnlySelection())},plugins:ro`;
    const { mode, selection } = parseScopesToMode(csv);
    expect(selection.plugins).toBe("ro");
    expect(mode).toBe("custom");
  });

  it("old all-:ro keys that include plugins:ro stay custom with plugins ticked", () => {
    const { mode, selection } = parseScopesToMode("plugins:ro,kb:ro");
    expect(mode).toBe("custom");
    expect(selection.plugins).toBe("ro");
  });
});
