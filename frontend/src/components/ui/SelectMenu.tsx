import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";
import { CheckMark } from "./Menu";

/** Rough panel height budget used only to decide whether to flip above the
 * trigger — the panel itself still scrolls past this via `max-h`. */
const PANEL_MAX_H = 300;

export type SelectMenuItem<T extends string | number> = {
  value: T;
  label: string;
  hint?: string;
};

type TriggerArgs = {
  open: boolean;
  ref: React.RefObject<HTMLButtonElement | null>;
  toggleProps: {
    "aria-haspopup": "listbox";
    "aria-expanded": boolean;
    onClick: () => void;
    onKeyDown: (e: React.KeyboardEvent) => void;
  };
};

type Position = { top?: number; bottom?: number; left?: number; right?: number; minWidth: number };

/**
 * Portal-based dropdown listbox — the `SelectMenu`-flavoured sibling of
 * `Menu`. Renders its panel into `document.body` via `createPortal` with
 * `position: fixed`, so it is never clipped by an `overflow-hidden` ancestor
 * (unlike `Menu`, which the account-menu language picker used to work
 * around with a native `<select>`). Used for value pickers with more than a
 * couple of options — queue/owner/responsible ticket pills, the account
 * menu's language choice.
 *
 * Position is derived from the trigger's `getBoundingClientRect()` on open
 * and flips above the trigger when there isn't `PANEL_MAX_H` px below; it
 * closes on scroll/resize rather than re-tracking, which keeps this simple.
 * The panel root carries `data-portal-menu` so a surrounding `Menu`'s
 * outside-pointerdown handler can recognize clicks into it as "inside".
 */
export function SelectMenu<T extends string | number>({
  items,
  value,
  onSelect,
  trigger,
  searchThreshold = 8,
  placeholder,
  align = "left",
  panelTestId,
  loading,
}: {
  items: SelectMenuItem<T>[];
  value?: T | null;
  onSelect: (value: T) => void;
  trigger: (args: TriggerArgs) => ReactNode;
  /** Search field appears automatically once there are more items than this. */
  searchThreshold?: number;
  placeholder?: string;
  align?: "left" | "right";
  panelTestId?: string;
  loading?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const [pos, setPos] = useState<Position | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  const showSearch = items.length > searchThreshold;

  const filtered = useMemo(() => {
    if (!showSearch || !query.trim()) return items;
    const q = query.trim().toLowerCase();
    return items.filter(
      (i) => i.label.toLowerCase().includes(q) || (i.hint ?? "").toLowerCase().includes(q),
    );
  }, [items, query, showSearch]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setHighlight(0);
  }, []);

  // Position relative to the trigger's current rect, computed synchronously
  // before paint so the panel never flashes at the wrong spot.
  useLayoutEffect(() => {
    if (!open) return;
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const flip = spaceBelow < PANEL_MAX_H && rect.top > spaceBelow;
    setPos({
      top: flip ? undefined : rect.bottom + 4,
      bottom: flip ? window.innerHeight - rect.top + 4 : undefined,
      left: align === "right" ? undefined : rect.left,
      right: align === "right" ? window.innerWidth - rect.right : undefined,
      minWidth: rect.width,
    });
  }, [open, align]);

  useEffect(() => {
    if (open && showSearch) searchRef.current?.focus();
  }, [open, showSearch]);

  // Outside pointer-down + Escape close; scroll/resize just closes rather
  // than re-tracking the trigger — simplest thing that avoids a stale panel.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        triggerRef.current?.focus();
      }
    };
    const onScrollOrResize = () => close();
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
    };
  }, [open, close]);

  const select = (item: SelectMenuItem<T>) => {
    onSelect(item.value);
    close();
  };

  const onPanelKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = filtered[highlight];
      if (item) select(item);
    }
  };

  const onTriggerKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setOpen(true);
    }
  };

  return (
    <>
      {trigger({
        open,
        ref: triggerRef,
        toggleProps: {
          "aria-haspopup": "listbox",
          "aria-expanded": open,
          onClick: () => setOpen((o) => !o),
          onKeyDown: onTriggerKeyDown,
        },
      })}
      {open &&
        createPortal(
          <div
            ref={panelRef}
            role="listbox"
            data-portal-menu
            data-testid={panelTestId}
            onKeyDown={onPanelKeyDown}
            // React bubbles portal events through the *component* tree, not
            // the DOM tree — without this, a click on an option here would
            // still reach a clickable-row ancestor's onClick (e.g. a
            // TicketTable row's navigate-on-click) even though the panel is
            // rendered into document.body, nowhere near that row in the DOM.
            onClick={(e) => e.stopPropagation()}
            style={{
              position: "fixed",
              top: pos?.top,
              bottom: pos?.bottom,
              left: pos?.left,
              right: pos?.right,
              minWidth: pos?.minWidth,
            }}
            className="z-50 max-h-[300px] w-max overflow-auto rounded-xl border border-hairline bg-surface p-1 shadow-xl animate-route-in"
          >
            {showSearch && (
              <input
                ref={searchRef}
                type="text"
                value={query}
                placeholder={placeholder}
                aria-label={placeholder}
                data-testid={panelTestId ? `${panelTestId}-search` : undefined}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setHighlight(0);
                }}
                className="mb-1 w-full rounded-lg border border-hairline bg-surface-subtle px-2 py-1 text-[13px] text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
              />
            )}
            {loading ? (
              <p className="px-2.5 py-2 text-[13px] text-muted">…</p>
            ) : filtered.length === 0 ? (
              <p className="px-2.5 py-2 text-[13px] text-muted">{placeholder}</p>
            ) : (
              filtered.map((item, i) => (
                <button
                  key={item.value}
                  type="button"
                  role="option"
                  aria-selected={item.value === value}
                  tabIndex={-1}
                  data-testid={panelTestId ? `${panelTestId}-option-${item.value}` : undefined}
                  onMouseEnter={() => setHighlight(i)}
                  onClick={() => select(item)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-ink/90 transition-colors duration-100 focus:outline-none",
                    i === highlight && "bg-surface-subtle",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {item.label}
                    {item.hint && <span className="ml-1 text-muted">({item.hint})</span>}
                  </span>
                  {item.value === value && (
                    <span className="flex w-4 shrink-0 justify-center text-accent" aria-hidden>
                      <CheckMark />
                    </span>
                  )}
                </button>
              ))
            )}
          </div>,
          document.body,
        )}
    </>
  );
}
