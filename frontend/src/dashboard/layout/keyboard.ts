export type KeyAction =
  | { kind: "move" | "resize"; dx: number; dy: number }
  | { kind: "settings" };

/** Arrow keys move by one cell, Shift+Arrow resizes by one cell, Enter opens the settings. */
export function keyToAction(e: { key: string; shiftKey: boolean }): KeyAction | null {
  const kind = e.shiftKey ? "resize" : "move";
  switch (e.key) {
    case "ArrowLeft":
      return { kind, dx: -1, dy: 0 };
    case "ArrowRight":
      return { kind, dx: 1, dy: 0 };
    case "ArrowUp":
      return { kind, dx: 0, dy: -1 };
    case "ArrowDown":
      return { kind, dx: 0, dy: 1 };
    case "Enter":
      return { kind: "settings" };
    default:
      return null;
  }
}
