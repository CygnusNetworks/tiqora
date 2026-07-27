/**
 * API-key area scopes (mirrors backend tiqora.domain.api_key_scopes).
 *
 * Storage: comma-separated CSV. Null/empty = unrestricted.
 * Tokens: legacy read/write/mcp/* and area:ro / area:rw.
 */

export const API_KEY_AREAS = [
  "tickets",
  "customers",
  "kb",
  "calendar",
  "stats",
  "ai",
  "process",
  "channels",
  "events",
  "agents",
  "admin",
  "mcp",
  "compat",
] as const;

export type ApiKeyArea = (typeof API_KEY_AREAS)[number];
export type AreaLevel = "off" | "ro" | "rw";
export type ScopeMode = "unrestricted" | "read_only" | "custom";

export type AreaSelection = Record<ApiKeyArea, AreaLevel>;

export function emptyAreaSelection(): AreaSelection {
  return Object.fromEntries(API_KEY_AREAS.map((a) => [a, "off"])) as AreaSelection;
}

export function allReadOnlySelection(): AreaSelection {
  return Object.fromEntries(API_KEY_AREAS.map((a) => [a, "ro"])) as AreaSelection;
}

/** Serialize custom selection to CSV (null if unrestricted / empty). */
export function selectionToScopes(mode: ScopeMode, selection: AreaSelection): string | null {
  if (mode === "unrestricted") return null;
  if (mode === "read_only") {
    return API_KEY_AREAS.map((a) => `${a}:ro`).join(",");
  }
  const parts: string[] = [];
  for (const area of API_KEY_AREAS) {
    const level = selection[area];
    if (level === "ro" || level === "rw") parts.push(`${area}:${level}`);
  }
  return parts.length ? parts.join(",") : null;
}

export function parseScopesToMode(scopes: string | null | undefined): {
  mode: ScopeMode;
  selection: AreaSelection;
} {
  const selection = emptyAreaSelection();
  if (scopes == null || scopes.trim() === "" || scopes.trim() === "*") {
    return { mode: "unrestricted", selection };
  }
  const parts = scopes
    .split(",")
    .map((p) => p.trim().toLowerCase())
    .filter(Boolean);

  // Legacy coarse tokens
  if (parts.length === 1 && parts[0] === "read") {
    return { mode: "read_only", selection: allReadOnlySelection() };
  }
  if (parts.includes("write") || parts.includes("read") || parts.includes("mcp")) {
    // Expand legacy into custom matrix for editing
    if (parts.includes("write") || parts.includes("read")) {
      for (const area of API_KEY_AREAS) {
        if (area === "mcp" || area === "compat") continue;
        selection[area] = parts.includes("write") ? "rw" : "ro";
      }
    }
    if (parts.includes("mcp") || parts.includes("write")) {
      selection.mcp = "rw";
    }
  }

  for (const token of parts) {
    if (!token.includes(":")) continue;
    const [area, level] = token.split(":", 2) as [string, string];
    if ((API_KEY_AREAS as readonly string[]).includes(area) && (level === "ro" || level === "rw")) {
      selection[area as ApiKeyArea] = level;
    }
  }

  // Detect pure read-only preset
  const isReadOnly =
    API_KEY_AREAS.every((a) => selection[a] === "ro") &&
    !parts.some((p) => p.endsWith(":rw") || p === "write");
  if (isReadOnly) {
    return { mode: "read_only", selection: allReadOnlySelection() };
  }

  return { mode: "custom", selection };
}

/** Compact label for table column. */
export function scopesSummaryKey(scopes: string | null | undefined): "unrestricted" | "readOnly" | "custom" {
  const { mode } = parseScopesToMode(scopes);
  if (mode === "unrestricted") return "unrestricted";
  if (mode === "read_only") return "readOnly";
  return "custom";
}
