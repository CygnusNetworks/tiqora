import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import {
  applyRefineDiff,
  buildRefineDiff,
  countChanges,
  type RefineDiffGroup,
} from "@/lib/refineDiff";

export interface RefineDiffViewProps {
  before: string;
  after: string;
  /** Already translated tone label, e.g. "Förmlich". */
  toneLabel: string;
  onAccept: (text: string, stats: { total: number; on: number }) => void;
  onDiscard: () => void;
  /** Fires on mount and after every toggle with the text the current on/off
   * selection would produce, so a parent can send it without accepting. */
  onGroupsChange?: (text: string, stats: { total: number; on: number }) => void;
  /** Sizing from the parent (min-height of the editor). */
  className?: string;
}

type View = "inline" | "side";
type Column = "inline" | "old" | "new";
type ChangeGroup = Extract<RefineDiffGroup, { kind: "change" }>;

const CHANGE_BASE =
  "cursor-pointer rounded transition-shadow duration-100 hover:ring-1 hover:ring-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";
const INS = "rounded-sm bg-green/15 px-px text-green no-underline";
const DEL = "rounded-sm bg-red/15 px-px text-red line-through";
const OFF_DEL = "rounded-sm px-px text-ink outline-dashed outline-1 outline-muted";
const OFF_INS_SIDE = "rounded-sm px-px text-muted line-through";
// A turned-off pure insertion: keep the added text visible (struck through) so it can be restored.
const OFF_PLACEHOLDER = `${OFF_INS_SIDE} outline-dashed outline-1 outline-muted`;
const SEG_BTN =
  "px-2.5 py-1 text-xs transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";
const GHOST_BTN =
  "rounded px-2 py-1 text-muted transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/** Renders unchanged text; lines that start a line with ">" are quotes (muted). */
function SameText({ text, atLineStart }: { text: string; atLineStart: boolean }) {
  const lines = text.split(/(?<=\n)/);
  return (
    <>
      {lines.map((line, i) =>
        (i > 0 || atLineStart) && line.startsWith(">") ? (
          <span key={i} className="text-muted">
            {line}
          </span>
        ) : (
          line
        ),
      )}
    </>
  );
}

export function RefineDiffView({
  before,
  after,
  toneLabel,
  onAccept,
  onDiscard,
  onGroupsChange,
  className,
}: RefineDiffViewProps) {
  const { t } = useTranslation();
  const [groups, setGroups] = useState<RefineDiffGroup[]>(() =>
    buildRefineDiff(before, after),
  );
  const [view, setView] = useState<View>("inline");
  const [cur, setCur] = useState(-1);
  const curRef = useRef<HTMLElement | null>(null);

  // A new before/after pair starts a fresh review.
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    setGroups(buildRefineDiff(before, after));
    setCur(-1);
  }, [before, after]);

  useEffect(() => {
    curRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [cur, view]);

  const stats = useMemo(() => countChanges(groups), [groups]);
  const onGroupsChangeRef = useRef(onGroupsChange);
  onGroupsChangeRef.current = onGroupsChange;
  useEffect(() => {
    onGroupsChangeRef.current?.(applyRefineDiff(groups), stats);
  }, [groups, stats]);
  // group index of each change, so a change's ordinal maps to its slot
  const changeSlots = useMemo(
    () => groups.flatMap((g, i) => (g.kind === "change" ? [i] : [])),
    [groups],
  );

  const toggle = (slot: number) =>
    setGroups((gs) =>
      gs.map((g, i) => (i === changeSlots[slot] && g.kind === "change" ? { ...g, on: !g.on } : g)),
    );

  const step = (dir: 1 | -1) => {
    if (stats.total === 0) return;
    setCur((c) => (c < 0 ? (dir === 1 ? 0 : stats.total - 1) : (c + dir + stats.total) % stats.total));
  };

  const onKey = (slot: number) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle(slot);
    }
  };

  const renderChange = (g: ChangeGroup, slot: number, col: Column, key: number) => {
    const isCur = slot === cur;
    let content: ReactNode;
    if (col === "inline") {
      content = (
        <>
          {g.removed && <del className={g.on ? DEL : OFF_DEL}>{g.removed}</del>}
          {g.on && g.added && <ins className={INS}>{g.added}</ins>}
          {!g.on && !g.removed && g.added && <span className={OFF_PLACEHOLDER}>{g.added}</span>}
        </>
      );
    } else if (col === "old") {
      content = g.removed ? <del className={g.on ? DEL : OFF_DEL}>{g.removed}</del> : null;
    } else if (g.on) {
      content = g.added ? <ins className={INS}>{g.added}</ins> : null;
    } else {
      content = g.removed ? (
        <span className={OFF_INS_SIDE}>{g.removed}</span>
      ) : g.added ? (
        <span className={OFF_PLACEHOLDER}>{g.added}</span>
      ) : null;
    }
    // Keyboard/ARIA only on one instance per change (inline, or the left column).
    const interactive = col !== "new";
    return (
      <span
        key={key}
        ref={isCur && interactive ? curRef : undefined}
        data-testid="refine-review-change"
        role={interactive ? "button" : undefined}
        tabIndex={interactive ? 0 : undefined}
        aria-pressed={interactive ? g.on : undefined}
        aria-current={isCur ? "true" : undefined}
        title={t("ticket.refine.review.toggleHint")}
        onClick={() => toggle(slot)}
        onKeyDown={interactive ? onKey(slot) : undefined}
        className={cn(CHANGE_BASE, isCur && "ring-2 ring-accent")}
      >
        {content || <span aria-hidden="true">{"​"}</span>}
      </span>
    );
  };

  const renderColumn = (col: Column): ReactNode => {
    let slot = -1;
    let prevText = ""; // text preceding the current group, for quote detection
    return groups.map((g, i) => {
      if (g.kind === "same") {
        const atLineStart = prevText === "" || prevText.endsWith("\n");
        prevText = g.text;
        return <SameText key={i} text={g.text} atLineStart={atLineStart} />;
      }
      slot += 1;
      prevText = col === "old" ? g.removed : col === "new" ? (g.on ? g.added : g.removed) : g.on ? g.added : g.removed;
      return renderChange(g, slot, col, i);
    });
  };

  const bodyCls =
    "min-w-0 whitespace-pre-wrap break-words px-4 py-3 font-mono text-[12.5px] leading-relaxed";
  const countText =
    stats.on === stats.total
      ? t("ticket.refine.review.count", { count: stats.total })
      : t("ticket.refine.review.countPartial", { on: stats.on, total: stats.total });

  return (
    <div
      data-testid="refine-review"
      className={cn("flex min-w-0 flex-col bg-surface", className)}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-hairline bg-accent-dim px-3 py-2 text-xs">
        <span className="font-semibold text-accent">
          {t("ticket.refine.review.title", { tone: toneLabel })}
        </span>
        <span className="tabular-nums text-muted">
          {stats.total === 0 ? t("ticket.refine.review.noChanges") : countText}
        </span>
        <span className="flex-1" />
        <div
          role="group"
          className="inline-flex overflow-hidden rounded-md border border-hairline bg-surface"
        >
          {(["inline", "side"] as const).map((v) => (
            <button
              key={v}
              type="button"
              data-testid={`refine-review-view-${v}`}
              aria-pressed={view === v}
              onClick={() => setView(v)}
              className={cn(
                SEG_BTN,
                view === v ? "bg-accent-dim font-semibold text-accent" : "text-muted",
              )}
            >
              {t(`ticket.refine.review.${v}`)}
            </button>
          ))}
        </div>
        <button
          type="button"
          data-testid="refine-review-prev"
          aria-label={t("ticket.refine.review.prev")}
          disabled={stats.total === 0}
          onClick={() => step(-1)}
          className={GHOST_BTN}
        >
          ‹
        </button>
        <button
          type="button"
          data-testid="refine-review-next"
          aria-label={t("ticket.refine.review.next")}
          disabled={stats.total === 0}
          onClick={() => step(1)}
          className={GHOST_BTN}
        >
          ›
        </button>
        <button
          type="button"
          data-testid="refine-review-discard"
          onClick={onDiscard}
          className="rounded-md border border-hairline bg-surface px-2.5 py-1 text-ink transition-colors duration-100 hover:border-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          {t("ticket.refine.review.discard")}
        </button>
        <button
          type="button"
          data-testid="refine-review-accept"
          onClick={() => onAccept(applyRefineDiff(groups), stats)}
          className="rounded-md border border-accent bg-accent px-2.5 py-1 font-semibold text-white transition-[filter] duration-100 hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          {t("ticket.refine.review.accept")}
        </button>
      </div>
      {view === "inline" ? (
        <div className={cn("flex-1", bodyCls)}>{renderColumn("inline")}</div>
      ) : (
        <div className="grid flex-1 grid-cols-1 sm:grid-cols-2">
          {(["old", "new"] as const).map((col) => (
            <div
              key={col}
              className={cn(
                "min-w-0",
                col === "new" && "border-t border-hairline sm:border-l sm:border-t-0",
              )}
            >
              <div className="border-b border-hairline bg-surface-subtle px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">
                {t(col === "old" ? "ticket.refine.review.before" : "ticket.refine.review.after")}
              </div>
              <div className={bodyCls}>{renderColumn(col)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
