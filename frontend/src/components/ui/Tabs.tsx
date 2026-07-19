import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

export type TabItem = {
  id: string;
  label: ReactNode;
  count?: number;
};

export function Tabs({
  items,
  value,
  onChange,
  className,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={cn(
        "flex flex-wrap gap-1 border-b border-border",
        className,
      )}
    >
      {items.map((item) => {
        const active = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(item.id)}
            className={cn(
              "relative -mb-px px-3 py-2 text-sm font-medium transition",
              active
                ? "border-b-2 border-accent text-accent"
                : "text-muted hover:text-ink",
            )}
          >
            {item.label}
            {item.count != null && (
              <span className="ml-1.5 text-xs tabular-nums text-muted">
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
