import { describe, it, expect } from "vitest";
import { md5 } from "@/lib/md5";
import { gravatarUrl, userEmailForAvatar } from "@/lib/gravatar";

describe("md5", () => {
  it("matches known digests (Gravatar-style inputs)", () => {
    // Empty string and classic test vectors.
    expect(md5("")).toBe("d41d8cd98f00b204e9800998ecf8427e");
    expect(md5("hello")).toBe("5d41402abc4b2a76b9719d911017c592");
  });
});

describe("gravatarUrl", () => {
  it("lowercases and trims the email before hashing", () => {
    const a = gravatarUrl("  Ada@Example.COM  ", { size: 80 });
    const b = gravatarUrl("ada@example.com", { size: 80 });
    expect(a).toBe(b);
    expect(a).toMatch(/^https:\/\/www\.gravatar\.com\/avatar\/[0-9a-f]{32}\?/);
  });

  it("uses d=404 by default so missing avatars can fall back to initials", () => {
    const url = gravatarUrl("agent@example.com")!;
    expect(url).toContain("d=404");
    expect(url).toContain("s=80");
  });

  it("embeds the size and optional default param", () => {
    const url = gravatarUrl("a@b.co", { size: 128, defaultParam: "mp" })!;
    expect(url).toContain("s=128");
    expect(url).toContain("d=mp");
  });

  it("returns null for empty / whitespace email", () => {
    expect(gravatarUrl("")).toBeNull();
    expect(gravatarUrl("   ")).toBeNull();
    expect(gravatarUrl(null)).toBeNull();
    expect(gravatarUrl(undefined)).toBeNull();
  });

  it("hashes the well-known Gravatar demo address", () => {
    // md5("myemailaddress@example.com") is a documented Gravatar example.
    const hash = md5("myemailaddress@example.com");
    expect(hash).toBe("0bc83cb571cd1c50ba6f3e8a78ef1346");
    expect(gravatarUrl("MyEmailAddress@example.com")).toBe(
      `https://www.gravatar.com/avatar/${hash}?d=404&s=80`,
    );
  });
});

describe("userEmailForAvatar", () => {
  it("prefers explicit email over login", () => {
    expect(
      userEmailForAvatar({ email: "ada@example.com", login: "agent" }),
    ).toBe("ada@example.com");
  });

  it("falls back to login when it looks like an email", () => {
    expect(userEmailForAvatar({ login: "ada@example.com" })).toBe("ada@example.com");
  });

  it("returns undefined when no usable email is present", () => {
    expect(userEmailForAvatar({ login: "agent" })).toBeUndefined();
    expect(userEmailForAvatar(null)).toBeUndefined();
    expect(userEmailForAvatar(undefined)).toBeUndefined();
  });
});
