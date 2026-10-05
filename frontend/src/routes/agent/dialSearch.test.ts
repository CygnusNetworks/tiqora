import { describe, expect, it } from "vitest";
import { validateDialSearch } from "./dialSearch";

describe("validateDialSearch", () => {
  it("accepts a numeric number (JSON-parsed query value)", () => {
    expect(validateDialSearch({ number: 491717630944 }).number).toBe("491717630944");
  });
  it("keeps an E.164 string unchanged and trims", () => {
    expect(validateDialSearch({ number: "+491717630944" }).number).toBe("+491717630944");
    expect(validateDialSearch({ number: " +49 " }).number).toBe("+49");
  });
  it("defaults number to empty", () => {
    expect(validateDialSearch({}).number).toBe("");
  });
  it("drops empty, zero, text, negative, fractional and null tickets", () => {
    for (const ticket of ["", 0, "abc", -3, 1.5, null]) {
      expect(validateDialSearch({ ticket }).ticket).toBeUndefined();
    }
  });
  it("parses a ticket id", () => {
    expect(validateDialSearch({ ticket: "42" }).ticket).toBe(42);
    expect(validateDialSearch({ ticket: 42 }).ticket).toBe(42);
  });
});
