import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";

/** Hover this long before the card opens — long enough that sweeping the
 * pointer across the header does not flash every card. */
export const HOVER_OPEN_DELAY_MS = 250;
/** Grace period for moving the pointer from the trigger into the card. */
export const HOVER_CLOSE_DELAY_MS = 200;

const VIEWPORT_MARGIN = 8;

type PanelPos = { top?: number; bottom?: number; left: number; maxHeight: number };

type TriggerArgs = {
  open: boolean;
  ref: React.RefObject<HTMLButtonElement | null>;
  triggerProps: {
    "aria-haspopup": "dialog";
    "aria-expanded": boolean;
    onClick: () => void;
    onMouseEnter: () => void;
    onMouseLeave: () => void;
  };
};

/**
 * Peek-on-hover panel for the ticket header's AI chips.
 *
 * Hovering the trigger opens the card after a short delay and leaving both
 * trigger and card closes it again. A click on the trigger — or any click
 * inside the card — makes it stay open until an outside click or `Escape`,
 * so an agent can hover to glance at a summary and click to work with it.
 * Touch devices have no hover and simply get the click behaviour.
 *
 * Portal-rendered and positioned from the trigger rect like `Popover`, but
 * follows the trigger on scroll/resize instead of closing: the card holds
 * long text an agent reads while scrolling the page. Pointer-downs inside
 * nested portals (`[data-portal-menu]`, dialogs) do not count as outside.
 */
export function HoverCard({
  trigger,
  children,
  label,
  panelClassName,
  panelTestId,
}: {
  trigger: (args: TriggerArgs) => ReactNode;
  children: ReactNode;
  /** Accessible name for the panel (`aria-label`). */
  label?: string;
  panelClassName?: string;
  panelTestId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [sticky, setSticky] = useState(false);
  const [pos, setPos] = useState<PanelPos | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const close = useCallback(() => {
    clearTimer();
    setOpen(false);
    setSticky(false);
  }, [clearTimer]);

  const scheduleOpen = useCallback(() => {
    clearTimer();
    timer.current = setTimeout(() => setOpen(true), HOVER_OPEN_DELAY_MS);
  }, [clearTimer]);

  const scheduleClose = useCallback(() => {
    if (sticky) return;
    clearTimer();
    timer.current = setTimeout(() => setOpen(false), HOVER_CLOSE_DELAY_MS);
  }, [clearTimer, sticky]);

  useEffect(() => clearTimer, [clearTimer]);

  const place = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom - VIEWPORT_MARGIN;
    const spaceAbove = rect.top - VIEWPORT_MARGIN;
    const flip = spaceBelow < 240 && spaceAbove > spaceBelow;
    const width = panelRef.current?.offsetWidth ?? 0;
    setPos({
      top: flip ? undefined : rect.bottom + 6,
      bottom: flip ? window.innerHeight - rect.top + 6 : undefined,
      left: Math.max(
        VIEWPORT_MARGIN,
        Math.min(rect.left, window.innerWidth - width - VIEWPORT_MARGIN),
      ),
      maxHeight: Math.max(160, (flip ? spaceAbove : spaceBelow) - 6),
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    place();
  }, [open, place]);

  // Second pass once the panel has a width, so the right-edge clamp is exact.
  useLayoutEffect(() => {
    if (open && pos && panelRef.current) {
      const left = Math.max(
        VIEWPORT_MARGIN,
        Math.min(
          triggerRef.current?.getBoundingClientRect().left ?? pos.left,
          window.innerWidth - panelRef.current.offsetWidth - VIEWPORT_MARGIN,
        ),
      );
      if (left !== pos.left) setPos({ ...pos, left });
    }
  }, [open, pos]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      // A menu or confirm dialog opened from inside the card is not an
      // outside click; another hover card is.
      const nested =
        target instanceof Element
          ? target.closest('[data-portal-menu], [role="dialog"], [role="alertdialog"]')
          : null;
      if (nested && !nested.hasAttribute("data-hover-card")) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // A nested menu handles its own Escape first.
      if (document.activeElement?.closest("[data-portal-menu]:not([data-hover-card])")) return;
      close();
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, close, place]);

  return (
    <>
      {trigger({
        open,
        ref: triggerRef,
        triggerProps: {
          "aria-haspopup": "dialog",
          "aria-expanded": open,
          onClick: () => {
            clearTimer();
            if (open && sticky) {
              close();
            } else {
              setOpen(true);
              setSticky(true);
            }
          },
          onMouseEnter: () => {
            if (!open) scheduleOpen();
            else clearTimer();
          },
          onMouseLeave: () => {
            if (open) scheduleClose();
            else clearTimer();
          },
        },
      })}
      {open &&
        createPortal(
          <div
            ref={panelRef}
            role="dialog"
            aria-label={label}
            data-testid={panelTestId}
            data-portal-menu
            data-hover-card
            onMouseEnter={clearTimer}
            onMouseLeave={scheduleClose}
            onPointerDown={() => setSticky(true)}
            style={{
              position: "fixed",
              top: pos?.top,
              bottom: pos?.bottom,
              left: pos?.left ?? 0,
              maxHeight: pos?.maxHeight,
              visibility: pos ? "visible" : "hidden",
            }}
            className={cn(
              "z-50 w-[min(36rem,calc(100vw-1rem))] overflow-y-auto rounded-xl border border-hairline bg-surface p-4 text-left shadow-xl animate-route-in",
              panelClassName,
            )}
          >
            {children}
          </div>,
          document.body,
        )}
    </>
  );
}
