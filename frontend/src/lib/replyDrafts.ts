import { useCallback, useMemo } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import {
  COMPOSE_ACTION,
  formDraftApi,
  type FormDraftOut,
} from "./formDraftApi";

/**
 * Unsent reply drafts, keyed per ticket+article and stored server-side in
 * `tiqora_form_draft`.
 *
 * `ReplyDialog` keeps its composer state in React, which survives a
 * backdrop click only as long as the component stays mounted — navigating
 * away or reloading loses it silently. This store makes an in-progress
 * reply an explicit, observable fact so the UI can point at it: the
 * article views render a placeholder for it, and the reply buttons switch
 * to "continue draft".
 *
 * Everything hangs off one query per ticket, so the placeholders, the
 * button badges and the open dialog all read the same cache entry and a
 * ticket costs exactly one request. Writes update that entry optimistically
 * — the composer autosaves on a debounce and must not wait for a round trip
 * to show a badge — and are reconciled with the server's row afterwards.
 */

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

/** The part of a draft that travels in `FormDraftOut.content` as JSON. */
type ReplyDraftContent = Omit<ReplyDraft, "ticketId" | "articleId" | "updatedAt">;

export function draftKey(ticketId: number, articleId: number): string {
  return `${ticketId}:${articleId}`;
}

export function ticketDraftsKey(ticketId: number) {
  return ["ticket", ticketId, "form-drafts"] as const;
}

/* ── Wire format ───────────────────────────────────────────────────────── */

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Rows we cannot make sense of are dropped rather than rendered as empty
 * drafts — the column is free-form and an older build (or a direct API
 * user) may have written something else entirely.
 */
function fromRow(row: FormDraftOut): ReplyDraft | null {
  if (row.article_id === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(row.content);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== "object") return null;
  const c = parsed as Partial<ReplyDraftContent>;
  if (typeof c.body !== "string") return null;
  const changed = Date.parse(row.changed);
  return {
    ticketId: row.ticket_id,
    articleId: row.article_id,
    replyAll: c.replyAll === true,
    subject: str(c.subject),
    body: c.body,
    to: str(c.to),
    cc: str(c.cc),
    bcc: str(c.bcc),
    replyTo: str(c.replyTo),
    aiDraftId: typeof c.aiDraftId === "number" ? c.aiDraftId : null,
    updatedAt: Number.isNaN(changed) ? Date.now() : changed,
  };
}

function toContent(draft: Omit<ReplyDraft, "updatedAt">): string {
  const content: ReplyDraftContent = {
    replyAll: draft.replyAll,
    subject: draft.subject,
    body: draft.body,
    to: draft.to,
    cc: draft.cc,
    bcc: draft.bcc,
    replyTo: draft.replyTo,
    aiDraftId: draft.aiDraftId,
  };
  return JSON.stringify(content);
}

function newestFirst(drafts: ReplyDraft[]): ReplyDraft[] {
  return [...drafts].sort((a, b) => b.updatedAt - a.updatedAt);
}

function parseRows(rows: FormDraftOut[]): ReplyDraft[] {
  const out: ReplyDraft[] = [];
  for (const row of rows) {
    if (row.action !== COMPOSE_ACTION) continue;
    const draft = fromRow(row);
    if (draft) out.push(draft);
  }
  return newestFirst(out);
}

/* ── Cache helpers ─────────────────────────────────────────────────────── */

function readCache(qc: QueryClient, ticketId: number): ReplyDraft[] {
  return qc.getQueryData<ReplyDraft[]>(ticketDraftsKey(ticketId)) ?? [];
}

function writeCache(qc: QueryClient, ticketId: number, drafts: ReplyDraft[]) {
  qc.setQueryData(ticketDraftsKey(ticketId), newestFirst(drafts));
}

/** Synchronous read of an already-loaded draft (no fetch). */
export function getDraft(
  qc: QueryClient,
  ticketId: number,
  articleId: number,
): ReplyDraft | null {
  return readCache(qc, ticketId).find((d) => d.articleId === articleId) ?? null;
}

/* ── Hooks ─────────────────────────────────────────────────────────────── */

const EMPTY: ReplyDraft[] = [];

function useTicketDraftsQuery(ticketId: number) {
  return useQuery({
    queryKey: ticketDraftsKey(ticketId),
    queryFn: ({ signal }) => formDraftApi.list(ticketId, signal).then(parseRows),
    // The composer is the only writer and updates the cache itself, so
    // background refetching would only ever undo an in-flight edit.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

/**
 * Subscribes to every draft on one ticket, newest edit first. Mounting this
 * anywhere on the ticket page is what loads the cache the other hooks and
 * `getDraft` read; the shared query key means it costs one request per
 * ticket no matter how many components ask.
 */
export function useTicketReplyDrafts(ticketId: number): ReplyDraft[] {
  return useTicketDraftsQuery(ticketId).data ?? EMPTY;
}

/** Subscribes to one article's draft. */
export function useReplyDraft(
  ticketId: number,
  articleId: number,
): ReplyDraft | null {
  const drafts = useTicketReplyDrafts(ticketId);
  return useMemo(
    () => drafts.find((d) => d.articleId === articleId) ?? null,
    [drafts, articleId],
  );
}

/**
 * True once the ticket's drafts are known, so a composer may seed from
 * them. Seeding earlier would show the server's fresh reply and then
 * overwrite whatever the agent started typing when the draft arrives.
 */
export function useReplyDraftsLoaded(ticketId: number): boolean {
  const { isSuccess, isError } = useTicketDraftsQuery(ticketId);
  // On error we let the composer proceed from the server-rendered reply
  // rather than block forever on a draft we will never get.
  return isSuccess || isError;
}

export function useSaveReplyDraft() {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: (draft: Omit<ReplyDraft, "updatedAt">) =>
      formDraftApi.upsert(draft.ticketId, {
        action: COMPOSE_ACTION,
        article_id: draft.articleId,
        content: toContent(draft),
      }),
    onSuccess: (row, draft) => {
      const saved = fromRow(row);
      if (!saved) return;
      // Adopt the server's `changed` timestamp, but only while the cache
      // still holds the body we just sent — a later keystroke has already
      // written a newer optimistic entry that must not be rolled back.
      const current = getDraft(qc, draft.ticketId, draft.articleId);
      if (!current || current.body !== draft.body) return;
      writeCache(qc, draft.ticketId, [
        ...readCache(qc, draft.ticketId).filter(
          (d) => d.articleId !== draft.articleId,
        ),
        saved,
      ]);
    },
  });
  const { mutate } = mutation;
  return useCallback(
    (draft: Omit<ReplyDraft, "updatedAt">) => {
      writeCache(qc, draft.ticketId, [
        ...readCache(qc, draft.ticketId).filter(
          (d) => d.articleId !== draft.articleId,
        ),
        { ...draft, updatedAt: Date.now() },
      ]);
      mutate(draft);
    },
    [qc, mutate],
  );
}

export function useClearReplyDraft() {
  const qc = useQueryClient();
  const { mutate } = useMutation({
    mutationFn: ({
      ticketId,
      articleId,
    }: {
      ticketId: number;
      articleId: number;
    }) => formDraftApi.remove(ticketId, COMPOSE_ACTION, articleId),
  });
  return useCallback(
    (ticketId: number, articleId: number) => {
      if (!getDraft(qc, ticketId, articleId)) return;
      writeCache(
        qc,
        ticketId,
        readCache(qc, ticketId).filter((d) => d.articleId !== articleId),
      );
      mutate({ ticketId, articleId });
    },
    [qc, mutate],
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
