import { useCallback, useState } from "react";
import type { RefineResult } from "./RefineControls";

type Stats = { total: number; on: number };

/**
 * State shared by the three composers for "review a refine before applying it":
 * the open review, the last accepted one (for "Änderungen anzeigen") and a
 * counter used as the `key` of `RefineDiffView` so every refine opens fresh.
 */
export function useRefineReview(setBody: (text: string) => void) {
  const [review, setReview] = useState<RefineResult | null>(null);
  const [applied, setApplied] = useState<{ result: RefineResult; stats: Stats } | null>(
    null,
  );
  const [reviewKey, setReviewKey] = useState(0);

  const open = useCallback((result: RefineResult) => {
    setReviewKey((k) => k + 1);
    setReview(result);
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
    /** Drops both — after a send or a reset to the baseline. */
    clear: () => {
      setReview(null);
      setApplied(null);
    },
  };
}
