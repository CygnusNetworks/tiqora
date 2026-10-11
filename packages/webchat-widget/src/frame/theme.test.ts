import { describe, expect, it } from "vitest";
import { applyTheme } from "./theme";

describe("theme", () => {
  it("applies theme through CSSOM and rejects bad colours", () => {
    const el = document.createElement("div");
    applyTheme(el, { primaryColor: "#ff0000", mode: "dark" }, "rtl");
    expect(el.style.getPropertyValue("--tq-primary")).toBe("#ff0000");
    expect(el.getAttribute("dir")).toBe("rtl");
    expect(el.dataset.theme).toBe("dark");
    applyTheme(el, { primaryColor: "red;x:y", mode: "auto" }, "ltr");
    expect(el.style.getPropertyValue("--tq-primary")).toBe("#2563eb");
    expect(el.dataset.theme).toBe("auto");
    expect(el.getAttribute("style")).not.toContain("x:y");
  });
});
