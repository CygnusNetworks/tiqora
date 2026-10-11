import { describe, expect, it } from "vitest";
import { galleryPlacementForWidth } from "./placement";

describe("galleryPlacementForWidth (window width only)", () => {
  it.each([
    [390, "none"],
    [639, "none"],
    [640, "overlay"],
    [1023, "overlay"],
    [1024, "overlay"],
    [1150, "overlay"],
    [1279, "overlay"],
    [1280, "side"],
    [1920, "side"],
  ] as const)("%d px -> %s", (width, expected) => {
    expect(galleryPlacementForWidth(width)).toBe(expected);
  });
});
