/**
 * The agent's saved instructions for custom AI summaries, and whether the
 * regular summary is pinned open in the ticket header. Both are display
 * preferences kept per browser (same approach as `./refineTone`), never
 * ticket state.
 */

const PROMPTS_KEY = "tiqora-ai-summary-prompts";
const PINNED_KEY = "tiqora-ai-summary-pinned";

/** Keeps the preset row to one or two lines in the summary card. */
export const MAX_SAVED_PROMPTS = 8;

export function loadSavedPrompts(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(PROMPTS_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.filter((p): p is string => typeof p === "string" && p.trim() !== "")
      : [];
  } catch {
    return [];
  }
}

export function saveSavedPrompts(prompts: string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PROMPTS_KEY, JSON.stringify(prompts.slice(0, MAX_SAVED_PROMPTS)));
  } catch {
    // private mode / SSR — ignore
  }
}

/** Newest first, no duplicates, capped at `MAX_SAVED_PROMPTS`. */
export function addSavedPrompt(prompts: string[], prompt: string): string[] {
  const trimmed = prompt.trim();
  if (!trimmed) return prompts;
  return [trimmed, ...prompts.filter((p) => p !== trimmed)].slice(0, MAX_SAVED_PROMPTS);
}

export function loadSummaryPinned(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(PINNED_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveSummaryPinned(pinned: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PINNED_KEY, pinned ? "1" : "0");
  } catch {
    // private mode / SSR — ignore
  }
}
