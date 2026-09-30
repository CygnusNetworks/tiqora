import { diffWordsWithSpace } from "diff";

export type RefineDiffGroup =
  | { kind: "same"; text: string }
  | { kind: "change"; removed: string; added: string; on: boolean };

const DIFF_TIMEOUT_MS = 300;
const DIFF_MAX_EDIT_LENGTH = 4000;

/** Fallback when the word diff is too expensive: keep the shared prefix and
 * suffix (cheap) and treat everything between as one change. */
function wholeTextChange(before: string, after: string): RefineDiffGroup[] {
  const max = Math.min(before.length, after.length);
  let start = 0;
  while (start < max && before[start] === after[start]) start++;
  let end = 0;
  while (
    end < max - start &&
    before[before.length - 1 - end] === after[after.length - 1 - end]
  )
    end++;
  const out: RefineDiffGroup[] = [];
  if (start > 0) out.push({ kind: "same", text: before.slice(0, start) });
  out.push({
    kind: "change",
    removed: before.slice(start, before.length - end),
    added: after.slice(start, after.length - end),
    on: true,
  });
  if (end > 0) out.push({ kind: "same", text: before.slice(before.length - end) });
  return out;
}

/** Word-level diff of `before` vs `after`, grouped for click-to-toggle review.
 * Consecutive removed/added parts form one change; a whitespace-only unchanged
 * part between two changes is absorbed so "a b" -> "c d" is one group. */
export function buildRefineDiff(before: string, after: string): RefineDiffGroup[] {
  // jsdiff is O(ND): a full rewrite of a long text would block the UI for
  // seconds. Bound the search; past the bound show one big change instead.
  const parts = diffWordsWithSpace(before, after, {
    timeout: DIFF_TIMEOUT_MS,
    maxEditLength: DIFF_MAX_EDIT_LENGTH,
  });
  if (!parts) return wholeTextChange(before, after);
  const out: RefineDiffGroup[] = [];
  parts.forEach((part, i) => {
    const last = out[out.length - 1];
    if (!part.added && !part.removed) {
      const next = parts[i + 1];
      if (
        last?.kind === "change" &&
        /^\s+$/.test(part.value) &&
        next &&
        (next.added || next.removed)
      ) {
        last.removed += part.value;
        last.added += part.value;
        return;
      }
      if (last?.kind === "same") last.text += part.value;
      else out.push({ kind: "same", text: part.value });
      return;
    }
    let group = last;
    if (group?.kind !== "change") {
      group = { kind: "change", removed: "", added: "", on: true };
      out.push(group);
    }
    if (part.removed) group.removed += part.value;
    else group.added += part.value;
  });
  return out;
}

export function applyRefineDiff(groups: RefineDiffGroup[]): string {
  return groups
    .map((g) => (g.kind === "same" ? g.text : g.on ? g.added : g.removed))
    .join("");
}

export function countChanges(groups: RefineDiffGroup[]): { total: number; on: number } {
  let total = 0;
  let on = 0;
  for (const g of groups) {
    if (g.kind !== "change") continue;
    total += 1;
    if (g.on) on += 1;
  }
  return { total, on };
}
