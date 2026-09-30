# Refine Diff Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After "Verfeinern", the composer shows a word-level comparison of old vs new text; the agent accepts all, discards all, or clicks individual changes away before accepting.

**Architecture:** Frontend-only. `RefineControls` stops writing the refined text into the editor directly when the parent opts in via a new `onRefined` callback. The parent renders a new `RefineDiffView` in place of the textarea until the agent accepts or discards. A pure helper `buildRefineDiff(before, after)` (jsdiff `diffWordsWithSpace`) produces change groups; `applyRefineDiff(groups)` rebuilds the text from the groups' on/off state. After accepting, the existing "undo" row in `RefineControls` also offers "Änderungen anzeigen".

**Tech Stack:** React 18, TypeScript, Tailwind tokens, `diff` (jsdiff, npm, ships its own types in v8), Vitest + Testing Library.

**Spec:** Mockup https://claude.ai/artifact/HqM7HfKMKHCR3j3MVSibUV (approved by the user 2026-09-30). The implementation must match its behaviour: review state opens after refine; toolbar "✦ Verfeinert · {Tonfall} · N Änderungen", view toggle "Im Text" / "Nebeneinander", ‹ › change navigation, "Verwerfen", "Übernehmen"; click on a change toggles it; quotes stay unchanged; after accept a bar "Verfeinert ({Tonfall}) · k von N Änderungen übernommen · Änderungen anzeigen · ↩ Rückgängig" which disappears on manual edit.

## Global Constraints

- Commit directly to `main`; never push to GitHub.
- `cd frontend && pnpm lint && pnpm test --run` must pass. Never run prettier/biome on `frontend/`.
- Styling only via Tailwind theme tokens (`accent`, `accent-dim`, `green`, `red`, `hairline`, `surface`, `surface-subtle`, `muted`, `ink`); opacity modifiers like `bg-green/15` work because tokens use `themeColor()`. `cn` is plain clsx (no tailwind-merge) — never pass conflicting classes.
- New i18n keys in `en.json` + `de.json` under `ticket.refine.review.*`, then propagate per skill `tiqora-i18n-key-propagation`.
- Adding the `diff` dependency: `pnpm add diff` in `frontend/`. If pnpm refuses due to `minimumReleaseAge`, pick the newest version old enough (see skill `pnpm-minimum-release-age-lockfile-verification`); do not disable the setting.
- The editor stays a plain `<textarea>` (`MentionTextarea`); the diff view is a separate read-only element swapped in.

## Code facts

- `frontend/src/components/agent/RefineControls.tsx:42` — component; stores `beforeRefine` (:67, :94), applies via `onChange` (:92-96), undo (:110-127); variants `split`/`toolbar`, `mode="call_note"`.
- `frontend/src/lib/replyQuote.ts` — `segmentBody` (:44), `applyRefined` (:92); refined body = own text refined + quotes untouched.
- Call sites: `frontend/src/components/agent/ReplyDialog.tsx:790` (editor at :743, textarea `MentionTextarea rows={12} bare className="font-mono text-[12.5px] ..."`), `frontend/src/routes/agent/NewTicketPage.tsx:868`, `frontend/src/components/agent/phone/PhoneCallDialog.tsx:355`.
- Tone labels: `ticket.refine.tone.*` in `de.json:744-763`.

## Review Focus

- Refine result identical to input (0 changes): no review state; show a short inline message "Keine Änderungen vorgeschlagen" and leave the text as is.
- Second refine while a previous refine was accepted: the comparison compares against the text right before THIS refine (not the original), undo restores that text.
- Send while the review is open (ReplyDialog): accept current on/off selection first, then send exactly that text. Test it.
- Text with newlines/paragraphs and quote blocks: `white-space: pre-wrap`, quotes rendered muted with no change marks. Test: body with `> ` quote lines produces no change groups inside the quote.
- Very long text: word diff of ~5k words must not freeze (jsdiff is O(ND); fine) — add one test with a 3000-word input finishing under the default test timeout.

---

### Task 1: Diff helper + `RefineDiffView` component

**Files:**
- Modify: `frontend/package.json`, `pnpm-lock.yaml` (add `diff`)
- Create: `frontend/src/lib/refineDiff.ts`
- Create: `frontend/src/lib/refineDiff.test.ts`
- Create: `frontend/src/components/agent/RefineDiffView.tsx`
- Create: `frontend/src/components/agent/RefineDiffView.test.tsx`
- Modify: `frontend/src/i18n/locales/en.json`, `de.json` (+ propagation)

**Interfaces:**
- Produces:
```ts
// refineDiff.ts
export type RefineDiffGroup =
  | { kind: "same"; text: string }
  | { kind: "change"; removed: string; added: string; on: boolean };
export function buildRefineDiff(before: string, after: string): RefineDiffGroup[];
export function applyRefineDiff(groups: RefineDiffGroup[]): string; // same→text, change→on?added:removed
export function countChanges(groups: RefineDiffGroup[]): { total: number; on: number };
```
```tsx
// RefineDiffView.tsx
export interface RefineDiffViewProps {
  before: string;
  after: string;
  toneLabel: string;              // already translated, e.g. "Förmlich"
  onAccept: (text: string, stats: { total: number; on: number }) => void;
  onDiscard: () => void;
  className?: string;             // sizing from the parent (min-height of the editor)
}
export function RefineDiffView(props: RefineDiffViewProps): JSX.Element;
```

- [ ] **Step 1: Failing tests for `refineDiff.ts`:**
```ts
import { buildRefineDiff, applyRefineDiff, countChanges } from "./refineDiff";
test("identical text has no changes", () => {
  expect(countChanges(buildRefineDiff("a b", "a b")).total).toBe(0);
});
test("all on reproduces after, all off reproduces before", () => {
  const before = "Hallo Herr Becker,\n\ndanke für ihre Nachricht.";
  const after = "Sehr geehrter Herr Becker,\n\nvielen Dank für Ihre Nachricht.";
  const g = buildRefineDiff(before, after);
  expect(applyRefineDiff(g)).toBe(after);
  expect(applyRefineDiff(g.map(x => x.kind === "change" ? { ...x, on: false } : x))).toBe(before);
});
test("adjacent word changes separated by one space form one group", () => {
  const g = buildRefineDiff("sehr gut hier", "ganz toll hier");
  expect(countChanges(g).total).toBe(1);
});
test("mixed toggles", () => {
  const g = buildRefineDiff("eins zwei drei vier", "EINS zwei DREI vier");
  const first = g.findIndex(x => x.kind === "change");
  const toggled = g.map((x, i) => (i === first && x.kind === "change" ? { ...x, on: false } : x));
  expect(applyRefineDiff(toggled)).toBe("eins zwei DREI vier");
});
test("quote lines unchanged produce no groups inside them", () => {
  const q = "\n\n> Am 29.09. schrieb X:\n> Hallo";
  const g = buildRefineDiff("hallo welt" + q, "Hallo Welt" + q);
  const last = g[g.length - 1];
  expect(last.kind).toBe("same");
  expect(last.kind === "same" && last.text.endsWith("> Hallo")).toBe(true);
});
test("3000 words is fast", () => {
  const w = Array.from({ length: 3000 }, (_, i) => `wort${i}`).join(" ");
  const g = buildRefineDiff(w, w.replace("wort10 ", "WORT10 "));
  expect(countChanges(g).total).toBe(1);
});
```
- [ ] **Step 2: Run** `pnpm test --run refineDiff` → FAIL.
- [ ] **Step 3: Implement** with `diffWordsWithSpace` from `diff`. Group consecutive added/removed parts into one `change`; a whitespace-only unchanged part sitting between two changed parts is absorbed into the group (appended to both `removed` and `added`) so "a b"→"c d" is one group.
- [ ] **Step 4: Failing tests for `RefineDiffView`:** renders `<del>`/`<ins>` marks; header shows "✦ Verfeinert · Förmlich · 3 Änderungen"; clicking a change toggles it (count text becomes "2 von 3 Änderungen aktiv"); "Übernehmen" calls `onAccept(textWithToggles, {total:3,on:2})`; "Verwerfen" calls `onDiscard`; "Nebeneinander" shows two columns labelled "Vorher"/"Nachher"; ‹ › buttons move a focus ring (`aria-current="true"`) across changes; change spans are keyboard-operable (`role="button"`, Enter/Space toggle).
- [ ] **Step 5: Implement** `RefineDiffView` following the mockup: review bar `bg-accent-dim border-b border-hairline text-xs`; body `font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap break-words px-4 py-3` (matches textarea font); `<ins>` = `bg-green/15 text-green no-underline rounded-sm`, `<del>` = `bg-red/15 text-red line-through rounded-sm`; turned-off change shows `removed` text with a dashed outline and hides `added` (inline view); side-by-side: grid 2 columns, 1 column below `sm`. Lines starting with `>` in `same` groups rendered `text-muted`. testids: `refine-review`, `refine-review-accept`, `refine-review-discard`, `refine-review-view-inline`, `refine-review-view-side`, `refine-review-prev`, `refine-review-next`, `refine-review-change`.
  i18n keys `ticket.refine.review.*`: `title` ("✦ Verfeinert · {{tone}}" / "✦ Refined · {{tone}}"), `count` ("{{count}} Änderungen" / "{{count}} changes", with `_one` plural forms), `countPartial` ("{{on}} von {{total}} Änderungen aktiv" / "{{on}} of {{total}} changes active"), `inline` ("Im Text"/"Inline"), `side` ("Nebeneinander"/"Side by side"), `before` ("Vorher"/"Before"), `after` ("Nachher"/"After"), `prev`, `next` (aria labels "Vorherige Änderung"/"Nächste Änderung"), `discard` ("Verwerfen"/"Discard"), `accept` ("Übernehmen"/"Apply"), `toggleHint` ("Klicken: Änderung zurücknehmen oder wieder übernehmen" / "Click to undo or restore this change"), `noChanges` ("Keine Änderungen vorgeschlagen" / "No changes suggested"), `applied` ("Verfeinert ({{tone}}) · {{on}} von {{total}} Änderungen übernommen" / "Refined ({{tone}}) · {{on}} of {{total}} changes applied"), `show` ("Änderungen anzeigen" / "Show changes").
- [ ] **Step 6: Run** tests → PASS; propagate i18n; `pnpm lint`. Commit: `feat(ui): word-level diff view for refined replies`.

### Task 2: Wire the review into `RefineControls` and the three composers

**Files:**
- Modify: `frontend/src/components/agent/RefineControls.tsx`
- Modify: `frontend/src/components/agent/ReplyDialog.tsx`
- Modify: `frontend/src/routes/agent/NewTicketPage.tsx`
- Modify: `frontend/src/components/agent/phone/PhoneCallDialog.tsx`
- Test: extend `RefineControls` tests and `ReplyDialog` tests (find with `ls frontend/src/components/agent/*.test.tsx`)

**Interfaces:**
- Consumes: `RefineDiffView`, `countChanges`, `buildRefineDiff` from Task 1.
- Produces (new optional props on `RefineControls`):
```ts
export interface RefineResult { before: string; after: string; tone: RefineTone; }
onRefined?: (result: RefineResult) => void;
// When given, RefineControls does NOT call onChange with the refined text;
// the parent opens RefineDiffView. When omitted, old behaviour (direct apply).
appliedStats?: { total: number; on: number } | null;
// Parent passes this after the user accepted, so the undo row can show
// "Verfeinert (Tonfall) · k von N Änderungen übernommen".
onShowChanges?: () => void;
// Renders the "Änderungen anzeigen" button in the undo row.
```
- Parent pattern (identical in all three composers):
```tsx
const [review, setReview] = useState<RefineResult | null>(null);
const [applied, setApplied] = useState<{ result: RefineResult; stats: {total:number; on:number} } | null>(null);
// editor slot:
{review ? (
  <RefineDiffView before={review.before} after={review.after} toneLabel={t(`ticket.refine.tone.${review.tone}`)}
    onAccept={(text, stats) => { setBody(text); setApplied({ result: review, stats }); setReview(null); }}
    onDiscard={() => setReview(null)} className="min-h-[..same as textarea..]" />
) : (<MentionTextarea ... onChange={(v) => { setBody(v); setApplied(null); }} />)}
<RefineControls ... onRefined={(r) => {
    if (r.before === r.after) { /* RefineControls shows noChanges itself */ return; }
    setReview(r);
  }}
  appliedStats={applied?.stats ?? null}
  onShowChanges={applied ? () => setReview(applied.result) : undefined} />
```
  Note: showing changes again after accept re-opens the review with the ORIGINAL after text and all changes on; accepting again overwrites the body. That matches the mockup.

- [ ] **Step 1: Failing tests:** (a) with `onRefined`, a successful refine does not call `onChange` and calls `onRefined({before, after, tone})` where `before` is the body at click time; (b) `before === after` → no `onRefined`, message `ticket.refine.review.noChanges` shown; (c) undo row shows `applied` text and "Änderungen anzeigen" when `appliedStats`/`onShowChanges` given; undo still restores `before`; (d) ReplyDialog: refine → review visible, textarea hidden; accept → textarea shows accepted text; send while review open sends the accepted text (assert the payload body).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** in `RefineControls` (keep old path when `onRefined` absent), then wire the three composers with the pattern above. ReplyDialog: the send handler checks `review` first — if open, compute `applyRefineDiff` of the current toggles. To make that possible without lifting toggle state out of `RefineDiffView`, give `RefineDiffView` an optional `onGroupsChange?: (text: string, stats) => void` that fires on every toggle and on mount; ReplyDialog keeps the latest `reviewText` in a ref and uses it when sending. Add that prop to `RefineDiffView` in this task with a test.
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run` → PASS. Commit: `feat(ui): review refined text before applying it`.
