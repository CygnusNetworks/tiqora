import { useEffect, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

const SIZE_CLASS = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-lg",
  xl: "max-w-2xl",
} as const;

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal dialog primitive. Beyond the chrome it owns the a11y mechanics every
 * dialog needs: body scroll-lock, focus moved into the panel on open (first
 * field, else first focusable), Tab cycling trapped inside, focus returned
 * to the opener on close, Escape/backdrop close. `footer` renders a fixed
 * action bar under the (scrollable) body so long forms never bury their
 * buttons.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Optional one-liner under the title — what this dialog does. */
  description?: string;
  children: ReactNode;
  /** Optional fixed action bar; content scrolls independently above it. */
  footer?: ReactNode;
  size?: keyof typeof SIZE_CLASS;
  className?: string;
}) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement as HTMLElement | null;

    // Move focus to the first form control (else first focusable) so typing
    // can start immediately; the close button is deliberately skipped.
    const raf = requestAnimationFrame(() => {
      const panel = panelRef.current;
      if (!panel || panel.contains(document.activeElement)) return;
      const preferred = panel.querySelector<HTMLElement>(
        "input, textarea, select, [data-autofocus]",
      );
      const target =
        preferred ??
        Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).find(
          (el) => !el.hasAttribute("data-dialog-close"),
        );
      target?.focus();
    });

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !panel.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !panel.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      openerRef.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="dialog-title"
    >
      <button
        type="button"
        tabIndex={-1}
        className="absolute inset-0 bg-black/50 backdrop-blur-[2px] motion-safe:animate-[dialog-fade_120ms_ease-out]"
        aria-label={t("common.back")}
        onClick={onClose}
      />
      <div
        ref={panelRef}
        className={cn(
          "relative z-10 flex max-h-[calc(100vh-3rem)] w-full flex-col rounded-lg border border-hairline bg-surface shadow-xl",
          "motion-safe:animate-[dialog-pop_140ms_ease-out]",
          SIZE_CLASS[size],
          className,
        )}
      >
        <div className="flex items-start justify-between gap-3 border-b border-hairline px-4 py-3">
          <div className="min-w-0">
            <h2 id="dialog-title" className="font-display text-sm font-semibold text-ink">
              {title}
            </h2>
            {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            aria-label={t("common.back")}
            data-dialog-close
            className="-mr-1 shrink-0"
          >
            ✕
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-sm text-ink">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-hairline bg-surface px-4 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
