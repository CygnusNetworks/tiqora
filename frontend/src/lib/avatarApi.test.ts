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
