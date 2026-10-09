import { describe, it, expect } from "vitest";
import { ApiError } from "./api";
import { apiErrorMessage } from "./avatarApi";

const FB = "fallback";
const err = (body: unknown) => new ApiError(400, body, "/x");

describe("apiErrorMessage", () => {
  it("uses a plain string body", () => expect(apiErrorMessage(err("Kaputt"), FB)).toBe("Kaputt"));
  it("falls back for an HTML proxy page", () =>
    expect(apiErrorMessage(err("<html>413</html>"), FB)).toBe(FB));
  it("reads {detail: string}", () => expect(apiErrorMessage(err({ detail: "Nein" }), FB)).toBe("Nein"));
  it("reads the first validation message", () =>
    expect(apiErrorMessage(err({ detail: [{ msg: "zu kurz" }] }), FB)).toBe("zu kurz"));
  it("reads {detail: {message}}", () =>
    expect(apiErrorMessage(err({ detail: { message: "Gesperrt" } }), FB)).toBe("Gesperrt"));
  it("reads {detail: {message, errors}}", () =>
    expect(apiErrorMessage(err({ detail: { message: "Felder prüfen", errors: { slug: "x" } } }), FB)).toBe("Felder prüfen"));
  it("reads {message}", () => expect(apiErrorMessage(err({ message: "Oben" }), FB)).toBe("Oben"));
  it("reads {message, path}", () =>
    expect(apiErrorMessage(err({ message: "Pfad", path: "/a" }), FB)).toBe("Pfad"));
  it("falls back for an unknown body", () => expect(apiErrorMessage(err({ foo: 1 }), FB)).toBe(FB));
  it("falls back for a non-ApiError", () => expect(apiErrorMessage(new Error("boom"), FB)).toBe(FB));
});

describe("apiErrorMessage (422 shapes)", () => {
  const err422 = (body: unknown) => new ApiError(422, body, "/x");

  it("reads a plain string detail", () => {
    expect(apiErrorMessage(err422({ detail: "Nicht erlaubt." }), "fb")).toBe("Nicht erlaubt.");
  });

  it("reads the first validation message of a list", () => {
    expect(apiErrorMessage(err422({ detail: [{ msg: "zu kurz" }, { msg: "x" }] }), "fb")).toBe("zu kurz");
  });

  it("reads {message}, {message, path} and {message, errors}", () => {
    expect(apiErrorMessage(err422({ detail: { message: "Schon beendet." } }), "fb")).toBe("Schon beendet.");
    expect(
      apiErrorMessage(err422({ detail: { message: "Unbekanntes Feld.", path: "where.field" } }), "fb"),
    ).toBe("Unbekanntes Feld.");
    expect(
      apiErrorMessage(err422({ detail: { message: "Bitte prüfen.", errors: [{ key: "a" }] } }), "fb"),
    ).toBe("Bitte prüfen.");
  });

  it("falls back for unknown shapes, HTML pages and non-ApiErrors", () => {
    expect(apiErrorMessage(err422({ detail: { foo: 1 } }), "fb")).toBe("fb");
    expect(apiErrorMessage(err422({ detail: { message: 5 } }), "fb")).toBe("fb");
    expect(apiErrorMessage(err422("<html>413</html>"), "fb")).toBe("fb");
    expect(apiErrorMessage(err422(null), "fb")).toBe("fb");
    expect(apiErrorMessage(new Error("boom"), "fb")).toBe("fb");
  });
});
