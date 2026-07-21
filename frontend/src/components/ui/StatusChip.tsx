import type { CSSProperties, HTMLAttributes, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { priorityColorVar, priorityIdFromName, priorityName } from "@/lib/priority";
import { stateColorVar, stateLabel } from "@/lib/status";

/**
 * Soft-chip: tinted pill used uniformly for ticket status and priority.
 * Theme-aware via CSS colour vars (`color-mix` against the token colour).
 */
export type SoftChipProps = {
  /** CSS colour value — typically `var(--color-state-…)` or `var(--color-prio-N)`. */
  color: string;
  children: ReactNode;
  className?: string;
} & Omit<HTMLAttributes<HTMLSpanElement>, "color">;

export function SoftChip({ color, children, className, style, ...rest }: SoftChipProps) {
  return (
    <span
      className={cn("inline-flex items-center leading-none", className)}
      style={
        {
          background: `color-mix(in srgb, ${color} 15%, transparent)`,
          color,
          fontWeight: 650,
          fontSize: 12,
          padding: "3px 11px",
          borderRadius: 999,
          whiteSpace: "nowrap",
          ...style,
        } as CSSProperties
      }
      {...rest}
    >
      {children}
    </span>
  );
}

export type StateChipProps = {
  state: string | null | undefined;
  /** Fallback when state is empty (default: render nothing). */
  empty?: ReactNode;
  className?: string;
} & Omit<HTMLAttributes<HTMLSpanElement>, "color" | "children">;

/** Soft-chip for a Znuny ticket state (localised label + state colour token). */
export function StateChip({ state, empty = null, className, ...rest }: StateChipProps) {
  const { t } = useTranslation();
  if (!state) return empty ? <>{empty}</> : null;
  return (
    <SoftChip color={stateColorVar(state)} className={className} data-kind="state" {...rest}>
      {stateLabel(t, state)}
    </SoftChip>
  );
}

export type PriorityChipProps = {
  priority: string | null | undefined;
  priorityId?: number | null | undefined;
  /** Fallback when priority is empty (default: render nothing). */
  empty?: ReactNode;
  className?: string;
} & Omit<HTMLAttributes<HTMLSpanElement>, "color" | "children">;

/** Soft-chip for a Znuny priority (bare name + priority colour ramp). */
export function PriorityChip({
  priority,
  priorityId,
  empty = null,
  className,
  ...rest
}: PriorityChipProps) {
  const label = priorityName(priority);
  if (!label) return empty ? <>{empty}</> : null;
  const resolvedId =
    priorityId != null && Number.isFinite(priorityId)
      ? priorityId
      : priorityIdFromName(priority);
  return (
    <SoftChip
      color={priorityColorVar(resolvedId)}
      className={className}
      data-kind="priority"
      {...rest}
    >
      {label}
    </SoftChip>
  );
}
