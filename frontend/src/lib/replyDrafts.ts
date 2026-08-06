import { useMemo, useSyncExternalStore } from "react";

/**
 * Unsent reply drafts, keyed per ticket+article and persisted in
 * localStorage.
 *
 * `ReplyDialog` keeps its composer state in React, which survives a
 * backdrop click only as long as the component stays mounted — navigating
 * away or reloading loses it silently. This store makes an in-progress
 * reply an explicit, observable fact so the UI can point at it: the
 * article views render a placeholder for it, and the reply buttons switch
 * to "continue draft".
 *
 * Writes are best-effort (a full/blocked localStorage must never break the
 * composer) but the in-memory snapshot always updates, so the current tab
 * stays consistent either way.
 */

const STORAGE_KEY = "tiqora-reply-drafts";

export type ReplyDraft = {
  ticketId: number;
  articleId: number;
  /** Which composer produced it — reopening restores the same mode. */
  replyAll: boolean;
  subject: string;
  /** Answer *and* quoted original, exactly as the textarea holds it. */
  body: string;
  to: string;
  cc: string;
  bcc: string;
  replyTo: string;
  /** AI draft this reply was seeded from, so sending still accepts it. */
  aiDraftId: number | null;
  /** Epoch ms of the last edit — drives the "edited HH:MM" label. */
  updatedAt: number;
};

type DraftMap = Record<string, ReplyDraft>;

export function draftKey(ticketId: number, articleId: number): string {
  return `${ticketId}:${articleId}`;
}

/* ── Store ─────────────────────────────────────────────────────────────── */

let cache: DraftMap | null = null;
const listeners = new Set<() => void>();

function isDraft(value: unknown): value is ReplyDraft {
  if (value === null || typeof value !== "object") return false;
  const d = value as Partial<ReplyDraft>;
  return (
    typeof d.ticketId === "number" &&
    typeof d.articleId === "number" &&
    typeof d.body === "string" &&
    typeof d.updatedAt === "number"
  );
}

function read(): DraftMap {
  if (cache) return cache;
  if (typeof window === "undefined") {
    cache = {};
    return cache;
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    const out: DraftMap = {};
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      // Drop anything that doesn't look like a draft rather than trusting
      // whatever an older build (or another tab) left behind.
      for (const [k, v] of Object.entries(parsed)) if (isDraft(v)) out[k] = v;
    }
    cache = out;
  } catch {
    cache = {};
  }
  return cache;
}

function commit(next: DraftMap) {
  cache = next;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Persistence is best-effort; the in-memory snapshot still stands.
    }
  }
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Snapshot for `useSyncExternalStore` — the cached map is replaced (never
 * mutated) on every write, so its identity is a valid change signal. */
function getSnapshot(): DraftMap {
  return read();
}

const EMPTY: DraftMap = {};

/** No drafts server-side; a stable empty map keeps hydration quiet. */
function getServerSnapshot(): DraftMap {
  return EMPTY;
}

/** Another tab wrote: drop the cache so the next read reloads from storage. */
function onStorage(e: StorageEvent) {
  if (e.key !== null && e.key !== STORAGE_KEY) return;
  cache = null;
  for (const l of listeners) l();
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", onStorage);
}

/* ── Reads/writes ──────────────────────────────────────────────────────── */

export function getDraft(ticketId: number, articleId: number): ReplyDraft | null {
  return read()[draftKey(ticketId, articleId)] ?? null;
}

/** Every draft for one ticket, newest edit first. */
export function getTicketDrafts(ticketId: number): ReplyDraft[] {
  return Object.values(read())
    .filter((d) => d.ticketId === ticketId)
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function saveDraft(draft: Omit<ReplyDraft, "updatedAt">): void {
  const next = { ...read() };
  next[draftKey(draft.ticketId, draft.articleId)] = { ...draft, updatedAt: Date.now() };
  commit(next);
}

export function clearDraft(ticketId: number, articleId: number): void {
  const current = read();
  const key = draftKey(ticketId, articleId);
  if (!(key in current)) return;
  const next = { ...current };
  delete next[key];
  commit(next);
}

/** Test seam — resets both the cache and the persisted map. */
export function resetDraftsForTest(): void {
  commit({});
}

/* ── Hooks ─────────────────────────────────────────────────────────────── */

/** Subscribes to one article's draft. */
export function useReplyDraft(ticketId: number, articleId: number): ReplyDraft | null {
  const drafts = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return drafts[draftKey(ticketId, articleId)] ?? null;
}

/** Subscribes to every draft on one ticket, newest edit first. */
export function useTicketReplyDrafts(ticketId: number): ReplyDraft[] {
  const drafts = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  // Memoised so the returned array keeps its identity between renders that
  // don't change the store — consumers map over it into React children.
  return useMemo(
    () =>
      Object.values(drafts)
        .filter((d) => d.ticketId === ticketId)
        .sort((a, b) => b.updatedAt - a.updatedAt),
    [drafts, ticketId],
  );
}

/* ── Preview ───────────────────────────────────────────────────────────── */

// The backend emits "On <date>, <who> wrote:" (tiqora.domain.quoting), but
// other languages put the verb before the name ("Am <date> schrieb <who>:"),
// so match the verb anywhere on a line that ends in a colon rather than
// directly in front of it.
const ATTRIBUTION_RE = /\b(wrote|schrieb|escribió|a écrit)\b.*:\s*$/i;

/**
 * The agent's own text, without the quoted original — the composer body is
 * `answer + "\n\n" + quote`, and the quote is the backend's plaintext
 * `> `-prefixed block preceded by an attribution line (see
 * `tiqora.domain.quoting`). Everything from the first quoted line onwards is
 * dropped, plus the attribution line that introduces it.
 */
export function draftPreview(body: string, maxChars = 160): string {
  const lines = body.split("\n");
  const quoteAt = lines.findIndex((l) => l.trimStart().startsWith(">"));
  const kept = quoteAt === -1 ? lines : lines.slice(0, quoteAt);
  while (kept.length > 0) {
    const last = kept[kept.length - 1].trim();
    if (last === "" || (quoteAt !== -1 && ATTRIBUTION_RE.test(last))) kept.pop();
    else break;
  }
  const text = kept.join(" ").replace(/\s+/g, " ").trim();
  return text.length > maxChars ? `${text.slice(0, maxChars)}…` : text;
}
