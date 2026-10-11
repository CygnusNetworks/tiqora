import { describe, expect, it } from "vitest";
import cases from "./linkUrlCases.json";
import { isSafeLinkUrl, linkKind } from "./linkUrl";

describe("isSafeLinkUrl mirrors backend safe_link_url (linkUrlCases.json)", () => {
  it.each(cases)("$url -> $ok", ({ url, ok }) => {
    expect(isSafeLinkUrl(url)).toBe(ok);
  });

  it("rejects non-strings", () => {
    expect(isSafeLinkUrl(undefined)).toBe(false);
    expect(isSafeLinkUrl(42)).toBe(false);
  });

  it("classifies how a safe url opens", () => {
    expect(linkKind("/agent")).toBe("internal");
    expect(linkKind("mailto:a@example.org")).toBe("mailto");
    expect(linkKind("https://example.org")).toBe("external");
  });
});
