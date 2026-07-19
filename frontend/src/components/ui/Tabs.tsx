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
    <div role="tablist" className={cn("flex flex-wrap items-center gap-1.5", className)}>
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
              "rounded-lg border px-3 py-1.5 text-[12.5px] font-medium transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
              active
                ? "border-accent bg-accent text-accent-ink"
                : "border-hairline bg-surface text-muted hover:border-accent/50 hover:text-ink",
            )}
          >
            {item.label}
            {item.count != null && (
              <span
                className={cn(
                  "ml-1.5 font-mono text-[11px] tabular-nums",
                  active ? "text-accent-ink/80" : "text-muted",
                )}
              >
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
