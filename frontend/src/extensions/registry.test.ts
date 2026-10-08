import { describe, it, expect } from "vitest";
import { createExtensionPoint, listExtensionPoints } from "./registry";

describe("extension registry", () => {
  it("registers, lists in order and rejects duplicates", () => {
    const p = createExtensionPoint<{ id: string }>("test.point", (x) => x.id);
    p.register({ id: "b" });
    p.register({ id: "a" }, "com.example.plugin");
    expect(p.list().map((x) => x.id)).toEqual(["b", "a"]);
    expect(p.owner("a")).toBe("com.example.plugin");
    expect(p.owner("b")).toBe("core");
    expect(p.get("a")).toEqual({ id: "a" });
    expect(p.get("zzz")).toBeUndefined();
    expect(() => p.register({ id: "a" })).toThrow(/test.point/);
    expect(listExtensionPoints().find((e) => e.key === "test.point")?.items).toHaveLength(2);
  });

  it("clearForTests empties the point", () => {
    const p = createExtensionPoint<{ id: string }>("test.clear", (x) => x.id);
    p.register({ id: "x" });
    p.clearForTests();
    expect(p.list()).toEqual([]);
    expect(p.owner("x")).toBeUndefined();
  });

  it("creating the same point key twice returns the same point", () => {
    const a = createExtensionPoint<{ id: string }>("test.same", (x) => x.id);
    a.register({ id: "one" });
    const b = createExtensionPoint<{ id: string }>("test.same", (x) => x.id);
    expect(b).toBe(a);
    expect(b.list()).toHaveLength(1);
  });
});
