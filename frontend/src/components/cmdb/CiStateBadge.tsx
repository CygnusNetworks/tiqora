import { Badge } from "@/components/ui/Badge";

/**
 * Deployment / incident state of a CI. Incident: operational reads neutral, warning amber, incident red
 * (existing tokens only). Deployment states are neutral labels.
 */
export function CiStateBadge({
  functionality,
  label,
  kind,
}: {
  functionality: string | null | undefined;
  label: string;
  kind: "deployment" | "incident";
}) {
  const tone =
    kind === "incident"
      ? functionality === "incident"
        ? "danger"
        : functionality === "warning"
          ? "warn"
          : "muted"
      : "default";
  return (
    <Badge tone={tone} data-testid={`ci-${kind}-badge`} data-functionality={functionality ?? ""}>
      {label}
    </Badge>
  );
}
