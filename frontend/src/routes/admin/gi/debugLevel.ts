/** Badge tone of a Znuny debugger level (the tones map to token classes). */
export function levelTone(level: string): "muted" | "default" | "accent" | "danger" {
  switch (level) {
    case "error":
      return "danger";
    case "notice":
      return "accent";
    case "info":
      return "default";
    default:
      return "muted";
  }
}
