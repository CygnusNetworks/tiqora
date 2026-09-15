import { describe, it, expect } from "vitest";
import { applyRefined, ownSections, segmentBody } from "./replyQuote";

/** Body exactly as `ReplyDialog` seeds it: blank answer area, then the
 * attribution line and the `> `-quoted original from `domain/quoting.py`. */
const SEEDED =
  "\n\nOn 2026-09-12 08:30, kunde@example.org wrote:\n> mein Internet geht nicht\n>\n> Gruss";

describe("segmentBody", () => {
  it("round-trips losslessly", () => {
    const body = `Hallo,\n\n${SEEDED}\nund noch was`;
    expect(
      segmentBody(body)
        .map((s) => s.text)
        .join(""),
    ).toBe(body);
  });

  it("treats a body without any quote as one own segment", () => {
    expect(segmentBody("Hallo Welt")).toEqual([
      { kind: "own", text: "Hallo Welt" },
    ]);
  });

  it("puts the attribution line into the quote segment", () => {
    const segments = segmentBody(`Danke!${SEEDED}`);
    expect(segments.map((s) => s.kind)).toEqual(["own", "quote"]);
    expect(segments[1].text).toContain("wrote:");
    expect(segments[1].text.startsWith("On 2026-09-12")).toBe(true);
    expect(segments[0].text).toBe("Danke!\n\n");
  });

  it("keeps a preceding sentence that is not an attribution in the own segment", () => {
    const segments = segmentBody("Das stimmt so nicht\n> zitat");
    expect(segments).toEqual([
      { kind: "own", text: "Das stimmt so nicht\n" },
      { kind: "quote", text: "> zitat" },
    ]);
  });

  it("splits interleaved inline quoting into alternating segments", () => {
    const body = "> Frage eins\nAntwort eins\n\n> Frage zwei\nAntwort zwei";
    expect(segmentBody(body).map((s) => s.kind)).toEqual([
      "quote",
      "own",
      "quote",
      "own",
    ]);
  });

  it("keeps own text written below the last quote", () => {
    const segments = segmentBody("> zitat\n\nViele Gruesse");
    expect(segments[segments.length - 1]).toEqual({
      kind: "own",
      text: "\nViele Gruesse",
    });
  });

  it("treats nested quote levels as quote", () => {
    expect(segmentBody(">> tief verschachtelt")).toEqual([
      { kind: "quote", text: ">> tief verschachtelt" },
    ]);
  });

  it("treats an indented quote marker as quote", () => {
    expect(segmentBody("  > eingerueckt")).toEqual([
      { kind: "quote", text: "  > eingerueckt" },
    ]);
  });
});

describe("ownSections", () => {
  it("numbers only the non-empty own segments, by segment index", () => {
    const segments = segmentBody(
      "> Frage\nAntwort\n\n> Noch eine\n\nUnd tschuess",
    );
    expect(ownSections(segments)).toEqual([
      { id: 1, text: "Antwort" },
      { id: 3, text: "Und tschuess" },
    ]);
  });

  it("skips a whitespace-only answer area", () => {
    expect(ownSections(segmentBody(SEEDED))).toEqual([]);
  });
});

describe("applyRefined", () => {
  it("replaces own text and leaves the quote byte-identical", () => {
    const body = `hallo ich bin der text${SEEDED}`;
    const segments = segmentBody(body);
    const refined = applyRefined(
      segments,
      new Map([[0, "Guten Tag, hier ist der Text."]]),
    );
    expect(refined).toBe(`Guten Tag, hier ist der Text.${SEEDED}`);
  });

  it("preserves the blank lines around each replaced section", () => {
    const segments = segmentBody("> zitat\n\nkurz\n\n");
    const refined = applyRefined(
      segments,
      new Map([[1, "Etwas ausfuehrlicher."]]),
    );
    expect(refined).toBe("> zitat\n\nEtwas ausfuehrlicher.\n\n");
  });

  it("leaves sections the model did not return untouched", () => {
    const segments = segmentBody("erstens\n> zitat\nzweitens");
    expect(applyRefined(segments, new Map([[2, "Zweitens."]]))).toBe(
      "erstens\n> zitat\nZweitens.",
    );
  });
});
