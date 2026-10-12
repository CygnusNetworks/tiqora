import { describe, expect, it } from "vitest";
import { claimName, mayRestore, visibleFields } from "./identity";
import { cfg } from "./testkit";

const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

describe("identity helpers", () => {
  it("reads the display name from the token payload, tolerating garbage", () => {
    expect(claimName(`${b64({ alg: "HS256" })}.${b64({ name: "Erika Muster", sub: "1" })}.sig`)).toBe("Erika Muster");
    expect(claimName(`h.${b64({ name: "  " })}.s`)).toBeNull();
    expect(claimName(`h.${b64({ name: 5 })}.s`)).toBeNull();
    expect(claimName("a.b.c")).toBeNull();
    expect(claimName("nonsense")).toBeNull();
    expect(claimName(`h.${b64({ name: "Ä".repeat(300) })}.s`)).toHaveLength(100);
  });

  it("hides the name and e-mail fields only while signed in", () => {
    const fields = [...cfg().prechat_fields, { key: "company", kind: "text" as const, label: "Firma", required: false, options: [] }, { key: "mail2", kind: "email" as const, label: "Mail", required: false, options: [] }];
    expect(visibleFields(fields, false).map((f) => f.key)).toEqual(["name", "email", "company", "mail2"]);
    expect(visibleFields(fields, true).map((f) => f.key)).toEqual(["company"]);
  });

  it("restores an identified chat only with a fresh identity", () => {
    expect(mayRestore({ identified: true }, false)).toBe(false);
    expect(mayRestore({ identified: true }, true)).toBe(true);
    expect(mayRestore({ identified: false }, false)).toBe(true);
  });
});
