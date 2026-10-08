import { describe, it, expect } from "vitest";
import { ApiError } from "@/lib/api";
import { apiErrorMessage } from "./avatarApi";

const err = (body: unknown) => new ApiError(422, body, "/x");

describe("apiErrorMessage", () => {
  it("reads a plain string detail", () => {
    expect(apiErrorMessage(err({ detail: "Nicht erlaubt." }), "fb")).toBe("Nicht erlaubt.");
  });

  it("reads the first validation message of a list", () => {
    expect(apiErrorMessage(err({ detail: [{ msg: "zu kurz" }, { msg: "x" }] }), "fb")).toBe("zu kurz");
  });

  it("reads {message}, {message, path} and {message, errors}", () => {
    expect(apiErrorMessage(err({ detail: { message: "Schon beendet." } }), "fb")).toBe("Schon beendet.");
    expect(
      apiErrorMessage(err({ detail: { message: "Unbekanntes Feld.", path: "where.field" } }), "fb"),
    ).toBe("Unbekanntes Feld.");
    expect(
      apiErrorMessage(err({ detail: { message: "Bitte prüfen.", errors: [{ key: "a" }] } }), "fb"),
    ).toBe("Bitte prüfen.");
  });

  it("falls back for unknown shapes, HTML pages and non-ApiErrors", () => {
    expect(apiErrorMessage(err({ detail: { foo: 1 } }), "fb")).toBe("fb");
    expect(apiErrorMessage(err({ detail: { message: 5 } }), "fb")).toBe("fb");
    expect(apiErrorMessage(err("<html>413</html>"), "fb")).toBe("fb");
    expect(apiErrorMessage(err(null), "fb")).toBe("fb");
    expect(apiErrorMessage(new Error("boom"), "fb")).toBe("fb");
  });
});
