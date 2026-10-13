import { Badge } from "@/components/ui/Badge";
import { ciStateColor } from "@/lib/ciDisplay";
import type { CatalogValue } from "@/lib/itsmApi";

// Badge tones are the tokens resolved through `themeColor()` in tailwind.config.js:
// green -> bg-green, warn -> bg-escalation, danger -> bg-danger.
const TONE = { green: "success", warn: "warn", danger: "danger", muted: "muted" } as const;

/** State chip of a configuration item or service (name from the catalogue, colour from its functionality). */
export function CiStateBadge({ state, className }: { state: CatalogValue | null | undefined; className?: string }) {
  if (!state) return <span className="text-muted">–</span>;
  const color = ciStateColor(state.functionality);
  return (
    <Badge tone={TONE[color]} className={className} data-testid="ci-state-badge" data-state-color={color}>
      {state.name}
    </Badge>
  );
}
