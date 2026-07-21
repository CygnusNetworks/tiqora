import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { cn } from "@/lib/cn";

/**
 * A lightweight dropdown menu primitive — no external dependency. Owns its own
 * open state and renders a trigger plus a popover panel that:
 *   • closes on outside pointer-down and on `Escape`,
 *   • moves roving focus across its items with ArrowUp/ArrowDown/Home/End,
 *   • focuses the first item when opened via keyboard,
 *   • returns focus to the trigger on close.
 *
 * The trigger is supplied as a render function so callers own the button
 * element (avatar, "+ New" pill, …) while still getting the wiring they need:
 * `{ open, ref, toggleProps }`. Spread `toggleProps` onto the button.
 *
 * Reused by the account menu, the language/theme choices within it, and the
 * New-ticket queue picker.
 */
type MenuContextValue = { close: () => void };
const MenuContext = createContext<MenuContextValue | null>(null);

type TriggerArgs = {
  open: boolean;
  ref: React.RefObject<HTMLButtonElement | null>;
  toggleProps: {
    "aria-haspopup": "menu";
    "aria-expanded": boolean;
    onClick: () => void;
    onKeyDown: (e: React.KeyboardEvent) => void;
  };
};

export function Menu({
  trigger,
  children,
  align = "right",
  panelClassName,
  panelTestId,
}: {
  trigger: (args: TriggerArgs) => ReactNode;
  children: ReactNode;
  align?: "left" | "right";
  panelClassName?: string;
  panelTestId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [autoFocus, setAutoFocus] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();

  const close = useCallback(() => setOpen(false), []);

  // Outside pointer-down + Escape close, and initial focus into the panel when
  // opened by keyboard. Focus returns to the trigger on close.
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (open && autoFocus) {
      itemAt(panelRef.current, 0)?.focus();
      setAutoFocus(false);
    }
  }, [open, autoFocus]);

  const onTriggerKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setAutoFocus(true);
      setOpen(true);
    }
  };

  const onPanelKeyDown = (e: React.KeyboardEvent) => {
    const items = itemList(panelRef.current);
    if (items.length === 0) return;
    const current = document.activeElement as HTMLElement | null;
    const idx = current ? items.indexOf(current) : -1;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(idx + 1 + items.length) % items.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(idx - 1 + items.length) % items.length]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1]?.focus();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div className="relative">
      {trigger({
        open,
        ref: triggerRef,
        toggleProps: {
          "aria-haspopup": "menu",
          "aria-expanded": open,
          onClick: () => setOpen((o) => !o),
          onKeyDown: onTriggerKeyDown,
        },
      })}
      {open && (
        <div
          ref={panelRef}
          role="menu"
          id={menuId}
          data-testid={panelTestId}
          onKeyDown={onPanelKeyDown}
          className={cn(
            "absolute z-50 mt-1.5 min-w-[13rem] overflow-hidden rounded-xl border border-hairline bg-surface p-1 shadow-xl animate-route-in",
            align === "right" ? "right-0" : "left-0",
            panelClassName,
          )}
        >
          <MenuContext.Provider value={{ close }}>{children}</MenuContext.Provider>
        </div>
      )}
    </div>
  );
}

function itemList(panel: HTMLElement | null): HTMLElement[] {
  if (!panel) return [];
  return Array.from(panel.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])'));
}

function itemAt(panel: HTMLElement | null, index: number): HTMLElement | undefined {
  return itemList(panel)[index];
}

/** A single actionable menu row. Closes the menu after firing `onSelect`
 * (unless `keepOpen`). Renders as a button; pass `selected` to show a check.
 * `highlight` marks an attention entry (e.g. Admin area) with accent fill. */
export function MenuItem({
  onSelect,
  children,
  icon,
  selected,
  keepOpen,
  danger,
  highlight,
  testId,
}: {
  onSelect?: () => void;
  children: ReactNode;
  icon?: ReactNode;
  selected?: boolean;
  keepOpen?: boolean;
  danger?: boolean;
  highlight?: boolean;
  testId?: string;
}) {
  const ctx = useContext(MenuContext);
  return (
    <button
      type="button"
      role="menuitem"
      tabIndex={-1}
      data-testid={testId}
      onClick={() => {
        onSelect?.();
        if (!keepOpen) ctx?.close();
      }}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors duration-100 focus:outline-none",
        highlight
          ? "bg-accent/15 font-semibold text-accent hover:bg-accent/25 focus-visible:bg-accent/25"
          : danger
            ? "text-danger hover:bg-danger/10 focus-visible:bg-danger/10"
            : "text-ink/90 hover:bg-surface-subtle focus-visible:bg-surface-subtle",
      )}
    >
      {icon != null && (
        <span
          className={cn(
            "flex w-4 shrink-0 justify-center text-[15px]",
            highlight ? "text-accent" : "text-muted",
          )}
          aria-hidden
        >
          {icon}
        </span>
      )}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {selected != null && (
        <span className="flex w-4 shrink-0 justify-center text-accent" aria-hidden>
          {selected ? <CheckMark /> : null}
        </span>
      )}
    </button>
  );
}

function CheckMark() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      width="0.85em"
      height="0.85em"
      aria-hidden="true"
    >
      <path d="m5 12.5 4.5 4.5L19 6.5" />
    </svg>
  );
}

/** Non-interactive section label (e.g. the "Sprache" / "Theme" group headers). */
export function MenuLabel({ children }: { children: ReactNode }) {
  return (
    <p className="px-2.5 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-muted">
      {children}
    </p>
  );
}

/** A header block for the account identity (name + login). */
export function MenuHeader({ children }: { children: ReactNode }) {
  return <div className="border-b border-hairline px-2.5 py-2">{children}</div>;
}

export function MenuSeparator() {
  return <div role="separator" className="my-1 border-t border-hairline" />;
}
