import { describe, expect, it } from "vitest";
import { formatVersion } from "./appVersion";

const FULL_SHA = "4520721e0c0ffee0c0ffee0c0ffee0c0ffee0c0f";

describe("formatVersion", () => {
  it("shows just the tag when the build is exactly on a tag", () => {
    expect(formatVersion("v1.4.0-0-g4520721", FULL_SHA)).toBe("v1.4.0");
  });

  it("shows tag + commit distance + short sha between tags", () => {
    expect(formatVersion("v1.4.0-5-g4520721", FULL_SHA)).toBe("v1.4.0 +5 · 4520721");
  });

  it("falls back to the describe g-sha when no full sha is provided", () => {
    expect(formatVersion("v1.4.0-5-g4520721", "")).toBe("v1.4.0 +5 · 4520721");
  });

  it("shows the bare abbreviated sha when no tag is reachable", () => {
    expect(formatVersion("4520721", FULL_SHA)).toBe("4520721");
  });

  it("uses the full sha (legacy builds that set only VITE_GIT_SHA)", () => {
    expect(formatVersion("", FULL_SHA)).toBe("4520721");
  });

  it("shows 'dev' for a local build with no provenance", () => {
    expect(formatVersion("", "")).toBe("dev");
  });
});
