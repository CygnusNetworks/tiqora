import { describe, it, expect } from "vitest";
import { currencySymbol, formatMoney, parseMoney, toEditableMoney } from "./money";

describe("currencySymbol", () => {
  it("maps the offered currencies", () => {
    expect(currencySymbol("EUR")).toBe("€");
    expect(currencySymbol("USD")).toBe("$");
    expect(currencySymbol("GBP")).toBe("£");
  });

  it("falls back to the code rather than inventing a symbol", () => {
    expect(currencySymbol("CHF")).toBe("CHF");
    expect(currencySymbol(null)).toBe("");
  });
});

describe("formatMoney", () => {
  it("pads a whole number to the currency's decimals", () => {
    expect(formatMoney(1, "de-DE")).toBe("1,00");
    expect(formatMoney(10, "en-GB")).toBe("10.00");
  });

  it("keeps an empty field empty — blank means 'not configured', not zero", () => {
    expect(formatMoney(null, "de-DE")).toBe("");
    expect(formatMoney("", "de-DE")).toBe("");
    expect(formatMoney(undefined, "de-DE")).toBe("");
  });

  it("gives per-token prices room for fractions of a cent", () => {
    expect(formatMoney(0.25, "de-DE", "price")).toBe("0,25");
    expect(formatMoney(0.0125, "de-DE", "price")).toBe("0,0125");
    // A budget stays at two, so 0.0125 there would round rather than mislead.
    expect(formatMoney(0.0125, "de-DE", "budget")).toBe("0,01");
  });
});

describe("parseMoney", () => {
  it("accepts either decimal separator", () => {
    expect(parseMoney("1,50")).toBe(1.5);
    expect(parseMoney("1.50")).toBe(1.5);
  });

  it("reads the last separator as the decimal point", () => {
    expect(parseMoney("1.234,56")).toBe(1234.56);
    expect(parseMoney("1,234.56")).toBe(1234.56);
  });

  it("returns null for an empty field, which is what clears the column", () => {
    expect(parseMoney("")).toBeNull();
    expect(parseMoney("   ")).toBeNull();
  });

  it("keeps zero, which is a real cap and not the same as unset", () => {
    expect(parseMoney("0")).toBe(0);
    expect(parseMoney("0,00")).toBe(0);
  });

  it("rejects what is not a non-negative number", () => {
    expect(parseMoney("abc")).toBe("invalid");
    expect(parseMoney("-5")).toBe("invalid");
  });
});

describe("toEditableMoney", () => {
  it("hands back the bare value, so no grouping is re-parsed as a decimal", () => {
    expect(toEditableMoney(1234.5)).toBe("1234.5");
    expect(toEditableMoney(null)).toBe("");
  });
});
