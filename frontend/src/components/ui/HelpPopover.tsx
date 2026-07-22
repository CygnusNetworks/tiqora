import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

/** Rough panel height budget used only to decide whether to flip above the trigger. */
const PANEL_MAX_H = 240;

type Position = { top?: number; bottom?: number; left?: number; right?: number };

/**
 * Small round ⓘ button next to a field label — click opens a portal-rendered
 * popover with a title, description and an optional "Default: …" footer.
 * Mirrors `SelectMenu`'s positioning (portal to `document.body`, flips above
 * the trigger when there isn't room below) so it is never clipped by an
 * `overflow-hidden` tab panel, and closes the same way: outside pointer-down,
 * `Escape`, or scroll/resize.
 */
export function HelpPopover({
  title,
  children,
  defaultHint,
  testId,
}: {
  title: string;
  children: ReactNode;
  defaultHint?: string;
  testId?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Position | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const close = useCallback(() => setOpen(false), []);

  useLayoutEffect(() => {
    if (!open) return;
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const flip = spaceBelow < PANEL_MAX_H && rect.top > spaceBelow;
    setPos({
      top: flip ? undefined : rect.bottom + 6,
      bottom: flip ? window.innerHeight - rect.top + 6 : undefined,
      left: Math.min(rect.left, window.innerWidth - 288 - 8),
    });
  }, [open]);

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

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t("common.help")}
        data-testid={testId}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-hairline text-[10px] font-semibold text-muted transition-colors hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      >
        i
      </button>
      {open &&
        createPortal(
          <div
            ref={panelRef}
            role="tooltip"
            data-testid={testId ? `${testId}-panel` : "help-popover-panel"}
            onClick={(e) => e.stopPropagation()}
            style={{
              position: "fixed",
              top: pos?.top,
              bottom: pos?.bottom,
              left: pos?.left,
            }}
            className="z-50 w-72 rounded-xl border border-hairline bg-surface p-3 text-left shadow-xl animate-route-in"
          >
            <p className="text-xs font-semibold text-ink">{title}</p>
            <div className="mt-1 text-xs leading-relaxed text-muted">{children}</div>
            {defaultHint && (
              <p
                className="mt-2 border-t border-hairline pt-1.5 text-[11px] text-muted"
                data-testid={testId ? `${testId}-default` : undefined}
              >
                {defaultHint}
              </p>
            )}
          </div>,
          document.body,
        )}
    </>
  );
}
