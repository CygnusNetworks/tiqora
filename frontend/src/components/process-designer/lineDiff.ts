// Minimal line diff for the version comparison (no diff library): trims the common head and tail,
// aligns the middle with an LCS and pairs removed with added lines side by side.

export type DiffRow = {
  kind: "same" | "removed" | "added" | "changed";
  left: string | null;
  right: string | null;
};

/** Above this many cells the LCS table is skipped and the middle is shown as one removed/added block. */
const MAX_CELLS = 2_000_000;

function lines(text: string): string[] {
  if (text === "") return [];
  const out = text.split("\n");
  if (out[out.length - 1] === "") out.pop();
  return out;
}

/** Rows of consecutive removed+added lines become `changed` pairs. */
function pair(ops: DiffRow[]): DiffRow[] {
  const out: DiffRow[] = [];
  let i = 0;
  while (i < ops.length) {
    if (ops[i].kind === "same") {
      out.push(ops[i++]);
      continue;
    }
    const removed: string[] = [];
    const added: string[] = [];
    while (i < ops.length && ops[i].kind !== "same") {
      if (ops[i].kind === "removed") removed.push(ops[i].left as string);
      else added.push(ops[i].right as string);
      i++;
    }
    const n = Math.max(removed.length, added.length);
    for (let k = 0; k < n; k++) {
      const l = removed[k] ?? null;
      const r = added[k] ?? null;
      out.push({ kind: l !== null && r !== null ? "changed" : l !== null ? "removed" : "added", left: l, right: r });
    }
  }
  return out;
}

export function diffLines(before: string, after: string): DiffRow[] {
  const a = lines(before);
  const b = lines(after);
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const am = a.slice(head, a.length - tail);
  const bm = b.slice(head, b.length - tail);

  const ops: DiffRow[] = a.slice(0, head).map((l) => ({ kind: "same", left: l, right: l }));
  if (am.length * bm.length > MAX_CELLS) {
    am.forEach((l) => ops.push({ kind: "removed", left: l, right: null }));
    bm.forEach((l) => ops.push({ kind: "added", left: null, right: l }));
  } else {
    const w = bm.length + 1;
    const lcs = new Uint32Array((am.length + 1) * w);
    for (let i = am.length - 1; i >= 0; i--) {
      for (let j = bm.length - 1; j >= 0; j--) {
        lcs[i * w + j] =
          am[i] === bm[j] ? lcs[(i + 1) * w + j + 1] + 1 : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < am.length || j < bm.length) {
      if (i < am.length && j < bm.length && am[i] === bm[j]) {
        ops.push({ kind: "same", left: am[i], right: bm[j] });
        i++;
        j++;
      } else if (j < bm.length && (i === am.length || lcs[i * w + j + 1] >= lcs[(i + 1) * w + j])) {
        ops.push({ kind: "added", left: null, right: bm[j++] });
      } else {
        ops.push({ kind: "removed", left: am[i++], right: null });
      }
    }
  }
  a.slice(a.length - tail).forEach((l) => ops.push({ kind: "same", left: l, right: l }));
  return pair(ops);
}
