import { describe, it, expect } from "vitest";
import { decodeEntities, stripHtml } from "./html";

describe("stripHtml", () => {
  it("strips tags and decodes entities", () => {
    expect(stripHtml("<p>Hi &amp; bye</p>")).toBe("Hi & bye");
  });
});

describe("decodeEntities", () => {
  it("decodes the basic entities without touching plain text", () => {
    expect(decodeEntities("a &lt; b &amp;&amp; b &gt; c")).toBe("a < b && b > c");
  });

  it("decodes only once per call — a double-escaped entity stays half-escaped", () => {
    // "&amp;gt;" is what a body containing the literal text "&gt;" looks like
    // once escaped by the API — one decodeEntities() call unescapes exactly
    // one layer, same as stripHtml's div.textContent would.
    expect(decodeEntities("&amp;gt;")).toBe("&gt;");
  });

  it("matches the preview regression case (escaped quote reply)", () => {
    // Fixture from the reported screenshot: a quoted-reply preview showing
    // literal &gt;/&amp; instead of the decoded characters.
    const escaped = "&gt; Danke &amp; Gruß, das Team";
    expect(decodeEntities(escaped)).toBe("> Danke & Gruß, das Team");
  });

  it("does not strip real tag-like text (unlike stripHtml)", () => {
    // decodeEntities is used on the plain-text branch, where a literal
    // "<" is not markup — it must survive, not be parsed away.
    expect(decodeEntities("Preis &lt; 5€")).toBe("Preis < 5€");
  });
});
