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
import { PopoverContext } from "./popoverContext";

/** Rough panel height budget used only to decide whether to flip the panel
 * above the trigger when there isn't room below (mirrors `Menu`). */
const PANEL_MAX_H = 260;

type PanelPos = { top?: number; bottom?: number; left?: number; right?: number };

type TriggerArgs = {
  open: boolean;
  ref: React.RefObject<HTMLButtonElement | null>;
  toggleProps: {
    "aria-haspopup": "dialog";
    "aria-expanded": boolean;
    onClick: () => void;
  };
};

/**
 * Portal-rendered popover for panels that hold *form controls* — the sibling
 * of `Menu`, which is a `role="menu"` list with roving focus and therefore the
 * wrong shell for inputs and comboboxes.
 *
 * Shares `Menu`'s behaviour otherwise: positioned from the trigger rect (so an
 * `overflow` ancestor can never clip it), flips above when there isn't room
 * below, and closes on outside pointer-down, `Escape`, scroll or resize.
 * Pointer-downs inside a nested portal panel (`[data-portal-menu]`, e.g. a
 * `SelectMenu` listbox opened from within) do not count as outside.
 *
 * Focus moves to the first focusable element in the panel on open and returns
 * to the trigger on close.
 */
export function Popover({
  trigger,
  children,
  align = "right",
  label,
  panelClassName,
  panelTestId,
}: {
  trigger: (args: TriggerArgs) => ReactNode;
  children: ReactNode;
  align?: "left" | "right";
  /** Accessible name for the panel (`aria-label`). */
  label?: string;
  panelClassName?: string;
  panelTestId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<PanelPos | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const close = useCallback(() => setOpen(false), []);

  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const flip = spaceBelow < PANEL_MAX_H && rect.top > spaceBelow;
    setPos({
      top: flip ? undefined : rect.bottom + 6,
      bottom: flip ? window.innerHeight - rect.top + 6 : undefined,
      left: align === "right" ? undefined : rect.left,
      right: align === "right" ? window.innerWidth - rect.right : undefined,
    });
  }, [open, align]);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      // A nested `SelectMenu`/`Menu` panel portals to `document.body`, outside
      // this panel's subtree — clicking it is not an outside click.
      if (target instanceof Element && target.closest("[data-portal-menu]")) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    // Fixed-positioned from the trigger rect on open, so page scroll would
    // strand the panel — close instead of re-tracking. Scrolling inside the
    // panel or a nested portal listbox must not close it.
    const onScroll = (e: Event) => {
      const target = e.target;
      if (target instanceof Node && panelRef.current?.contains(target)) return;
      const el =
        target instanceof Element ? target : target instanceof Node ? target.parentElement : null;
      if (el?.closest("[data-portal-menu]")) return;
      setOpen(false);
    };
    const onResize = () => setOpen(false);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open]);

  // Move focus into the panel once it is positioned and painted.
  useEffect(() => {
    if (!open || !pos) return;
    const first = panelRef.current?.querySelector<HTMLElement>(
      'input, select, textarea, button, [href], [tabindex]:not([tabindex="-1"])',
    );
    first?.focus();
  }, [open, pos]);

  const wasOpen = useRef(false);
  useEffect(() => {
    if (wasOpen.current && !open) triggerRef.current?.focus();
    wasOpen.current = open;
  }, [open]);

  return (
    <div className="relative">
      {trigger({
        open,
        ref: triggerRef,
        toggleProps: {
          "aria-haspopup": "dialog",
          "aria-expanded": open,
          onClick: () => setOpen((o) => !o),
        },
      })}
      {open &&
        pos &&
        createPortal(
          <div
            ref={panelRef}
            role="dialog"
            aria-label={label}
            data-testid={panelTestId}
            data-portal-menu
            style={{ position: "fixed", ...pos }}
            className={cn(
              "z-50 max-h-[min(22rem,80vh)] w-64 overflow-y-auto rounded-xl border border-hairline bg-surface p-3 text-left shadow-xl animate-route-in",
              panelClassName,
            )}
          >
            <PopoverContext.Provider value={{ close }}>{children}</PopoverContext.Provider>
          </div>,
          document.body,
        )}
    </div>
  );
}
