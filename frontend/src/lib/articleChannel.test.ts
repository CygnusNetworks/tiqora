import { describe, it, expect } from "vitest";
import type { ArticleListItem } from "@/lib/api";
import {
  emailFromAddress,
  formatFromAddress,
  formatToAddresses,
  initialsFor,
  senderDisplayName,
} from "./articleChannel";

describe("emailFromAddress", () => {
  it("extracts the address from a \"Name <mail@host>\" from_address", () => {
    expect(emailFromAddress("Ada Lovelace <ada@example.com>")).toBe("ada@example.com");
  });

  it("passes through a bare address", () => {
    expect(emailFromAddress("ada@example.com")).toBe("ada@example.com");
  });

  it("returns undefined for missing or unparseable input", () => {
    expect(emailFromAddress(null)).toBeUndefined();
    expect(emailFromAddress(undefined)).toBeUndefined();
    expect(emailFromAddress("not an address")).toBeUndefined();
  });
});

describe("senderDisplayName", () => {
  it("strips quotes from a quoted \"Last, First\" display name", () => {
    expect(senderDisplayName('"Luhmer, Bastian" <l@x.de>')).toBe("Luhmer, Bastian");
  });

  it("strips quotes from a single-quoted display name", () => {
    expect(senderDisplayName("'Netadmin StudNet Bonn' <n@y.de>")).toBe("Netadmin StudNet Bonn");
  });

  it("falls back to the bare address when there is no display name", () => {
    expect(senderDisplayName("mail@host.de")).toBe("mail@host.de");
  });

  it("returns null for missing input", () => {
    expect(senderDisplayName(null)).toBeNull();
    expect(senderDisplayName(undefined)).toBeNull();
    expect(senderDisplayName("")).toBeNull();
  });
});

describe("initialsFor", () => {
  const article = (from_address: string | null): ArticleListItem =>
    ({ from_address }) as ArticleListItem;

  it("takes the first letters of a quoted \"Last, First\" name", () => {
    expect(initialsFor(article('"Luhmer, Bastian" <l@x.de>'))).toBe("LB");
  });

  it("takes the first letters of a quoted multi-word name", () => {
    expect(initialsFor(article("'Netadmin StudNet Bonn' <n@y.de>"))).toBe("NS");
  });

  it("falls back to the local-part of a bare address", () => {
    expect(initialsFor(article("mail@host.de"))).toBe("MA");
  });

  it("falls back to a placeholder for missing input", () => {
    expect(initialsFor(article(null))).toBe("?");
  });
});

describe("formatFromAddress / formatToAddresses", () => {
  it("re-renders a quoted \"Name <email>\" header without the quotes", () => {
    expect(formatFromAddress('"Luhmer, Bastian" <l@x.de>')).toBe("Luhmer, Bastian <l@x.de>");
  });

  it("passes through a bare address unchanged", () => {
    expect(formatFromAddress("mail@host.de")).toBe("mail@host.de");
  });

  it("re-renders each recipient in a comma-joined To header without quotes", () => {
    expect(
      formatToAddresses('"Luhmer, Bastian" <l@x.de>, \'Netadmin StudNet Bonn\' <n@y.de>'),
    ).toBe("Luhmer, Bastian <l@x.de>, Netadmin StudNet Bonn <n@y.de>");
  });
});
