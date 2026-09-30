import { diffWordsWithSpace } from "diff";

export type RefineDiffGroup =
  | { kind: "same"; text: string }
  | { kind: "change"; removed: string; added: string; on: boolean };

/** Word-level diff of `before` vs `after`, grouped for click-to-toggle review.
 * Consecutive removed/added parts form one change; a whitespace-only unchanged
 * part between two changes is absorbed so "a b" -> "c d" is one group. */
export function buildRefineDiff(before: string, after: string): RefineDiffGroup[] {
  const parts = diffWordsWithSpace(before, after);
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
