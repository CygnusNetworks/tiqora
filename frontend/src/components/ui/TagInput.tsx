import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";

export type TagSuggestion = {
  name: string;
  /** Number of (visible) KB articles carrying this tag — shown as a hint. */
  count?: number;
};

/**
 * Tag picker: selected tags render as removable pills, typing filters a
 * suggestion dropdown (existing KB tags), Enter/comma on free text creates a
 * new tag name. Keyboard: ↑/↓ move the highlight, Enter picks it (or adds
 * the typed text when nothing is highlighted), Backspace on an empty input
 * removes the last pill.
 *
 * The dropdown is positioned absolutely inside the component (no portal) —
 * every current call site (KB editor, policy editor, KB list filter) lives
 * in a normally-overflowing page flow.
 */
export function TagInput({
  value,
  onChange,
  suggestions = [],
  placeholder,
  testId,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  suggestions?: TagSuggestion[];
  placeholder?: string;
  testId?: string;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const selectedLower = useMemo(() => new Set(value.map((t) => t.toLowerCase())), [value]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return suggestions
      .filter((s) => !selectedLower.has(s.name.toLowerCase()))
      .filter((s) => (q ? s.name.toLowerCase().includes(q) : true))
      .slice(0, 12);
  }, [suggestions, selectedLower, query]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      setOpen(false);
      setHighlight(-1);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const add = (raw: string) => {
    const name = raw.trim().replace(/,+$/, "").trim();
    if (!name || selectedLower.has(name.toLowerCase())) {
      setQuery("");
      return;
    }
    onChange([...value, name]);
    setQuery("");
    setHighlight(-1);
  };

  const remove = (tag: string) => {
    onChange(value.filter((t) => t !== tag));
    inputRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && highlight >= 0 && filtered[highlight]) add(filtered[highlight].name);
      else if (query.trim()) add(query);
    } else if (e.key === ",") {
      e.preventDefault();
      if (query.trim()) add(query);
    } else if (e.key === "Escape") {
      setOpen(false);
      setHighlight(-1);
    } else if (e.key === "Backspace" && query === "" && value.length > 0) {
      e.preventDefault();
      remove(value[value.length - 1]);
    }
  };

  return (
    <div ref={rootRef} className="relative" data-testid={testId}>
      <div
        className="flex min-h-[38px] w-full cursor-text flex-wrap items-center gap-1.5 rounded-md border border-hairline bg-surface-subtle px-2 py-1.5 focus-within:ring-1 focus-within:ring-accent"
        onClick={() => inputRef.current?.focus()}
      >
        {value.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 rounded-full bg-accent-dim px-2 py-0.5 text-[12px] font-medium text-accent"
            data-testid={testId ? `${testId}-pill-${tag}` : undefined}
          >
            {tag}
            <button
              type="button"
              aria-label={`remove ${tag}`}
              data-testid={testId ? `${testId}-remove-${tag}` : undefined}
              onClick={(e) => {
                e.stopPropagation();
                remove(tag);
              }}
              className="text-accent/70 hover:text-accent"
            >
              ✕
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
          value={query}
          placeholder={value.length === 0 ? placeholder : undefined}
          data-testid={testId ? `${testId}-input` : undefined}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setHighlight(-1);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className="min-w-[80px] flex-1 bg-transparent py-0.5 text-sm text-ink placeholder:text-muted focus:outline-none"
        />
      </div>
      {open && filtered.length > 0 && (
        <div
          role="listbox"
          data-testid={testId ? `${testId}-panel` : undefined}
          className="absolute left-0 top-full z-40 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-hairline bg-surface p-1 shadow-xl"
        >
          {filtered.map((s, i) => (
            <button
              key={s.name}
              type="button"
              role="option"
              aria-selected={i === highlight}
              tabIndex={-1}
              data-testid={testId ? `${testId}-option-${s.name}` : undefined}
              onMouseEnter={() => setHighlight(i)}
              onClick={() => {
                add(s.name);
                inputRef.current?.focus();
              }}
              className={cn(
                "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] text-ink/90",
                i === highlight && "bg-surface-subtle",
              )}
            >
              <span className="truncate">{s.name}</span>
              {typeof s.count === "number" && (
                <span className="shrink-0 text-[11px] tabular-nums text-muted">{s.count}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
