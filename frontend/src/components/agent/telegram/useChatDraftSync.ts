import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  chatDraftKey,
  useChatDraft,
  useClearChatDraft,
  useSaveChatDraft,
  type ChatDraft,
} from "@/lib/replyDrafts";

/**
 * Keeps the chat composer's unsent message in the server-side draft store:
 * restores it once when the lookup finishes, then autosaves on the same
 * 400 ms debounce as ReplyDialog. `markSent` drops the draft and voids any
 * autosave still queued from before the send; text left in the composer
 * after it is saved afresh.
 */
export function useChatDraftSync(
  ticketId: number,
  current: ChatDraft,
  restore: (draft: ChatDraft) => void,
) {
  const qc = useQueryClient();
  const { draft: stored, loaded } = useChatDraft(ticketId);
  const saveDraft = useSaveChatDraft();
  const clearDraft = useClearChatDraft();
  const seededRef = useRef(false);
  const sendEpochRef = useRef(0);
  // Re-arms the autosave after a send: whatever was typed while the request
  // was in flight survives it, and must be saved again even though the body
  // itself may not change any more.
  const [sentTick, setSentTick] = useState(0);
  const restoreRef = useRef(restore);
  restoreRef.current = restore;

  useEffect(() => {
    if (!loaded || seededRef.current) return;
    seededRef.current = true;
    if (stored) restoreRef.current(stored);
  }, [loaded, stored]);

  const { body, quoteArticleId, aiDraftId } = current;
  useEffect(() => {
    // Nothing to compare against before the restore — saving now would
    // overwrite the stored draft with an empty composer.
    if (!seededRef.current) return;
    const epoch = sendEpochRef.current;
    const timer = window.setTimeout(() => {
      if (sendEpochRef.current !== epoch) return;
      if (!body.trim() && quoteArticleId === null) {
        clearDraft(ticketId);
        return;
      }
      const saved = qc.getQueryData<ChatDraft | null>(chatDraftKey(ticketId));
      if (
        saved &&
        saved.body === body &&
        saved.quoteArticleId === quoteArticleId &&
        saved.aiDraftId === aiDraftId
      ) {
        return;
      }
      saveDraft(ticketId, { body, quoteArticleId, aiDraftId });
    }, 400);
    return () => window.clearTimeout(timer);
  }, [qc, ticketId, body, quoteArticleId, aiDraftId, saveDraft, clearDraft, loaded, sentTick]);

  const markSent = useCallback(() => {
    sendEpochRef.current += 1;
    clearDraft(ticketId);
    setSentTick((n) => n + 1);
  }, [clearDraft, ticketId]);

  return { markSent };
}
