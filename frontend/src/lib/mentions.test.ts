import { describe, it, expect } from "vitest";
import { mentionQueryAt, survivingMentions } from "./mentions";

describe("mentionQueryAt", () => {
  it("finds the token at the caret", () => {
    expect(mentionQueryAt("hallo @ad", 9)).toEqual({ start: 6, query: "ad" });
  });

  it("finds a bare @ that has just been typed", () => {
    expect(mentionQueryAt("hallo @", 7)).toEqual({ start: 6, query: "" });
  });

  it("matches at the very start of the text", () => {
    expect(mentionQueryAt("@di", 3)).toEqual({ start: 0, query: "di" });
  });

  it("ignores an @ inside an email address", () => {
    expect(mentionQueryAt("mail an bob@example.com", 23)).toBeNull();
  });

  it("ignores a token the caret has already moved past", () => {
    expect(mentionQueryAt("@ada schreibt", 13)).toBeNull();
  });

  it("stops at whitespace after the token", () => {
    expect(mentionQueryAt("@ada ", 5)).toBeNull();
  });

  it("accepts umlauts and dots in the query", () => {
    expect(mentionQueryAt("cc @jörg.m", 10)).toEqual({ start: 3, query: "jörg.m" });
  });

  it("reads from the caret, not the end of the text", () => {
    expect(mentionQueryAt("@ad rest", 3)).toEqual({ start: 0, query: "ad" });
  });
});

describe("survivingMentions", () => {
  const ada = { id: 1, name: "Ada Lovelace" };
  const bob = { id: 2, name: "Bob Stone" };

  it("keeps mentions still named in the body", () => {
    expect(survivingMentions("hi @Ada Lovelace", [ada])).toEqual([ada]);
  });

  it("drops a mention whose name was deleted again", () => {
    expect(survivingMentions("hi there", [ada])).toEqual([]);
  });

  it("keeps only the named ones", () => {
    expect(survivingMentions("@Bob Stone bitte schauen", [ada, bob])).toEqual([bob]);
  });

  it("collapses a person picked twice", () => {
    expect(survivingMentions("@Ada Lovelace @Ada Lovelace", [ada, ada])).toEqual([ada]);
  });
});
