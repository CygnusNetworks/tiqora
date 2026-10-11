import { expect, test } from "vitest";
import { keyToAction } from "./keyboard";

test("keys", () => {
  expect(keyToAction({ key: "ArrowRight", shiftKey: false })).toEqual({ kind: "move", dx: 1, dy: 0 });
  expect(keyToAction({ key: "ArrowLeft", shiftKey: false })).toEqual({ kind: "move", dx: -1, dy: 0 });
  expect(keyToAction({ key: "ArrowUp", shiftKey: false })).toEqual({ kind: "move", dx: 0, dy: -1 });
  expect(keyToAction({ key: "ArrowDown", shiftKey: true })).toEqual({ kind: "resize", dx: 0, dy: 1 });
  expect(keyToAction({ key: "Enter", shiftKey: false })).toEqual({ kind: "settings" });
  expect(keyToAction({ key: "a", shiftKey: false })).toBeNull();
});
