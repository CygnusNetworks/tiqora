import { useCallback, useRef, useState } from "react";
import type { RefineResult } from "./RefineControls";

type Stats = { total: number; on: number };

/**
 * State shared by the three composers for "review a refine before applying it":
 * the open review, the last accepted one (for "Änderungen anzeigen") and a
 * counter used as the `key` of `RefineDiffView` so every refine opens fresh.
 *
 * The open review's current on/off selection is tracked in a ref
 * (`onGroupsChange`), so a composer can submit "as marked" via `flush()`.
 */
export function useRefineReview(setBody: (text: string) => void) {
  const [review, setReview] = useState<RefineResult | null>(null);
  const [applied, setApplied] = useState<{ result: RefineResult; stats: Stats } | null>(
    null,
  );
  const [reviewKey, setReviewKey] = useState(0);
  const textRef = useRef("");
  const openRef = useRef(false);
  openRef.current = review !== null;
  const setBodyRef = useRef(setBody);
  setBodyRef.current = setBody;

  const open = useCallback((result: RefineResult) => {
    setReviewKey((k) => k + 1);
    setReview(result);
  }, []);

  const currentReviewText = useCallback(() => textRef.current, []);

  const clear = useCallback(() => {
    setReview(null);
    setApplied(null);
  }, []);

  return {
    review,
    reviewKey,
    applied,
    /** `RefineControls.onRefined`. Identical text never opens a review. */
    onRefined: (result: RefineResult) => {
      if (result.before === result.after) return;
      // A new refine supersedes the previous accepted one (its undo target
      // would be stale once this one is applied or discarded).
      setApplied(null);
      open(result);
    },
    accept: (text: string, stats: Stats) => {
      if (!review) return;
      setBody(text);
      setApplied({ result: review, stats });
      setReview(null);
    },
    discard: () => setReview(null),
    /** Re-opens the accepted refine, all changes on again. */
    showChanges: applied ? () => open(applied.result) : undefined,
    /** Any manual edit (or undo) ends the "applied" summary. */
    onEdit: (text: string) => {
      setBody(text);
      setApplied(null);
    },
    /** Pass to `RefineDiffView.onGroupsChange`. */
    onGroupsChange: (text: string) => {
      textRef.current = text;
    },
    /** Latest selection text of the open review (for draft persistence). */
    currentReviewText,
    /**
     * Submitting while a review is open = accept as marked: returns the
     * selection text, writes it to the body and closes the review. Returns
     * null when no review is open (use the regular body then).
     */
    flush: (): string | null => {
      if (!openRef.current) return null;
      const text = textRef.current;
      setBodyRef.current(text);
      setReview(null);
      setApplied(null);
      return text;
    },
    /** Drops both — after a send, a reset, or when another writer sets the body. */
    clear,
  };
}
