import { describe, it, expect } from "vitest";
import { emailFromAddress } from "./articleChannel";

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
