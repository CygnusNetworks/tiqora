/**
 * Splits a composer body into what the agent wrote and what is quoted, so the
 * "refine" action can rewrite the former and leave the latter untouched.
 *
 * Mail replies interleave: a quoted question, an answer below it, the next
 * quoted question, the next answer. So this is a segmentation, not a single
 * cut — `segmentBody` returns the alternating runs in order, and
 * `applyRefined` rebuilds the body from the ORIGINAL bytes of every quote
 * segment. Quoted text therefore never travels through the LLM's output path
 * at all; it cannot be altered even by a model that tries to.
 *
 * A quote line is one whose first non-blank character is `>` (so nested `>>`
 * and indented quotes count too). The attribution line that
 * `domain/quoting.py` writes above a quote ("On <date>, <who> wrote:") is
 * pulled into the quote segment — recognised by its trailing colon, so an
 * ordinary sentence written directly above a quote stays refinable.
 */

export type SegmentKind = "own" | "quote";

export type Segment = {
  kind: SegmentKind;
  /** Exact source text, line terminators included. Concatenating every
   * segment's `text` reproduces the input body byte for byte. */
  text: string;
};

/** A non-empty own segment offered to the model, keyed by its index in the
 * segment list so the reply can be mapped back without reordering. */
export type OwnSection = { id: number; text: string };

const QUOTE_LINE = /^[ \t]*>/;

/** Split keeping the terminators, so segments can be re-joined losslessly. */
function splitLines(body: string): string[] {
  return body.match(/[^\n]*\n|[^\n]+/g) ?? [];
}

function isAttribution(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.length > 0 && trimmed.endsWith(":");
}

export function segmentBody(body: string): Segment[] {
  const lines = splitLines(body);
  const kinds: SegmentKind[] = lines.map((l) =>
    QUOTE_LINE.test(l) ? "quote" : "own",
  );
  // Pull the attribution line into the quote run it introduces.
  for (let i = 0; i < kinds.length; i++) {
    if (
      kinds[i] === "quote" &&
      i > 0 &&
      kinds[i - 1] === "own" &&
      isAttribution(lines[i - 1])
    ) {
      kinds[i - 1] = "quote";
    }
  }

  const segments: Segment[] = [];
  for (let i = 0; i < lines.length; i++) {
    const last = segments[segments.length - 1];
    if (last && last.kind === kinds[i]) {
      last.text += lines[i];
    } else {
      segments.push({ kind: kinds[i], text: lines[i] });
    }
  }
  return segments;
}

export function ownSections(segments: Segment[]): OwnSection[] {
  const sections: OwnSection[] = [];
  segments.forEach((segment, id) => {
    if (segment.kind !== "own") return;
    const text = segment.text.trim();
    if (text.length === 0) return;
    sections.push({ id, text });
  });
  return sections;
}

/** Re-insert `refined` text into its own segment, keeping that segment's
 * surrounding blank lines so the paragraph structure around quotes survives. */
function reinsert(original: string, refined: string): string {
  const lead = /^\s*/.exec(original)?.[0] ?? "";
  const trail = /\s*$/.exec(original)?.[0] ?? "";
  return `${lead}${refined.trim()}${trail}`;
}

export function applyRefined(
  segments: Segment[],
  refined: Map<number, string>,
): string {
  return segments
    .map((segment, id) => {
      const replacement = refined.get(id);
      if (segment.kind !== "own" || replacement === undefined)
        return segment.text;
      return reinsert(segment.text, replacement);
    })
    .join("");
}
