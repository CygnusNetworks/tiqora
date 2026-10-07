import { describe, expect, it } from "vitest";
import {
  domainFromUrl,
  domainLogoUrl,
  emailDomain,
  personAvatarUrl,
  userEmailForAvatar,
} from "@/lib/avatar";
import { sha256 } from "@/lib/sha256";

describe("personAvatarUrl", () => {
  it("keys the proxy URL by the SHA-256 of the trimmed, lowercased address", () => {
    const hash = sha256("ada@example.com");
    expect(personAvatarUrl("  Ada@Example.COM  ")).toBe(`/api/v1/avatars/email/${hash}`);
    expect(personAvatarUrl("ada@example.com")).not.toContain("ada");
  });

  it("appends the version as a cache buster", () => {
    expect(personAvatarUrl("ada@example.com", "1700000000000")).toMatch(
      /^\/api\/v1\/avatars\/email\/[0-9a-f]{64}\?v=1700000000000$/,
    );
  });

  it("returns null for empty input", () => {
    expect(personAvatarUrl("")).toBeNull();
    expect(personAvatarUrl("   ")).toBeNull();
    expect(personAvatarUrl(null)).toBeNull();
    expect(personAvatarUrl(undefined)).toBeNull();
  });
});

describe("domainLogoUrl", () => {
  it("builds the lowercase domain URL, with optional version", () => {
    expect(domainLogoUrl("Example.COM")).toBe("/api/v1/avatars/domain/example.com");
    expect(domainLogoUrl("example.com", "5")).toBe("/api/v1/avatars/domain/example.com?v=5");
    expect(domainLogoUrl(null)).toBeNull();
  });
});

describe("emailDomain", () => {
  it("returns the lowercase domain part", () => {
    expect(emailDomain("Jane@Sub.Example.de")).toBe("sub.example.de");
  });

  it("returns null without a usable domain", () => {
    expect(emailDomain("jane")).toBeNull();
    expect(emailDomain("@example.com")).toBeNull();
    expect(emailDomain("jane@localhost")).toBeNull();
    expect(emailDomain(null)).toBeNull();
  });
});

describe("domainFromUrl", () => {
  it("takes the host, with or without a scheme, minus www.", () => {
    expect(domainFromUrl("https://www.Example.com/about")).toBe("example.com");
    expect(domainFromUrl("www.example.org")).toBe("example.org");
    expect(domainFromUrl("shop.example.net:8080")).toBe("shop.example.net");
  });

  it("returns null for empty or unusable values", () => {
    expect(domainFromUrl("")).toBeNull();
    expect(domainFromUrl(null)).toBeNull();
    expect(domainFromUrl("not a url")).toBeNull();
    expect(domainFromUrl("intranet")).toBeNull();
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
