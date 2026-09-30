import { describe, expect, test } from "vitest";
import { applyRefineDiff, buildRefineDiff, countChanges } from "./refineDiff";

describe("refineDiff", () => {
  test("identical text has no changes", () => {
    expect(countChanges(buildRefineDiff("a b", "a b")).total).toBe(0);
  });
  test("all on reproduces after, all off reproduces before", () => {
    const before = "Hallo Herr Becker,\n\ndanke für ihre Nachricht.";
    const after = "Sehr geehrter Herr Becker,\n\nvielen Dank für Ihre Nachricht.";
    const g = buildRefineDiff(before, after);
    expect(applyRefineDiff(g)).toBe(after);
    expect(
      applyRefineDiff(g.map((x) => (x.kind === "change" ? { ...x, on: false } : x))),
    ).toBe(before);
  });
  test("adjacent word changes separated by one space form one group", () => {
    const g = buildRefineDiff("sehr gut hier", "ganz toll hier");
    expect(countChanges(g).total).toBe(1);
  });
  test("mixed toggles", () => {
    const g = buildRefineDiff("eins zwei drei vier", "EINS zwei DREI vier");
    const first = g.findIndex((x) => x.kind === "change");
    const toggled = g.map((x, i) =>
      i === first && x.kind === "change" ? { ...x, on: false } : x,
    );
    expect(applyRefineDiff(toggled)).toBe("eins zwei DREI vier");
  });
  test("quote lines unchanged produce no groups inside them", () => {
    const q = "\n\n> Am 29.09. schrieb X:\n> Hallo";
    const g = buildRefineDiff("hallo welt" + q, "Hallo Welt" + q);
    const last = g[g.length - 1];
    expect(last.kind).toBe("same");
    expect(last.kind === "same" && last.text.endsWith("> Hallo")).toBe(true);
  });
  test("3000 words is fast", () => {
    const w = Array.from({ length: 3000 }, (_, i) => `wort${i}`).join(" ");
    const g = buildRefineDiff(w, w.replace("wort10 ", "WORT10 "));
    expect(countChanges(g).total).toBe(1);
  });
  test("countChanges counts on", () => {
    const g = buildRefineDiff("a x b y c", "a X b Y c");
    const off = g.map((x, i) => (i === g.findIndex((y) => y.kind === "change") && x.kind === "change" ? { ...x, on: false } : x));
    expect(countChanges(off)).toEqual({ total: 2, on: 1 });
  });
});

describe("buildRefineDiff on a full rewrite of long text", () => {
  test("finishes quickly and still round-trips", () => {
    const words = (p: string) =>
      Array.from({ length: 4000 }, (_, i) => `${p}${i}`).join(" ");
    const before = `Hallo\n\n${words("alt")}\n\nGruss`;
    const after = `Hallo\n\n${words("neu")}\n\nGruss`;
    const t0 = Date.now();
    const groups = buildRefineDiff(before, after);
    expect(Date.now() - t0).toBeLessThan(1500);
    expect(applyRefineDiff(groups)).toBe(after);
    expect(applyRefineDiff(groups.map((g) => (g.kind === "change" ? { ...g, on: false } : g)))).toBe(before);
    expect(countChanges(groups).total).toBeGreaterThan(0);
  });
});
