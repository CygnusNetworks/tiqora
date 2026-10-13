/** Trust level of a plugin package or installed plugin: label key, chip colour and source text. */
import type { TFunction } from "i18next";
import type { PluginAdmin, PluginPackage } from "@/lib/pluginsApi";

export type TrustLevel = NonNullable<PluginPackage["trust"]>;

const TRUST_COLOR: Record<TrustLevel, string> = {
  official: "var(--color-green)",
  verified_publisher: "var(--color-accent)",
  local: "var(--color-muted)",
  unsigned: "var(--color-warn)",
  invalid: "var(--color-danger)",
};

export function trustLabelKey(level: TrustLevel): string {
  return `admin.plugins.trust.${level}`;
}

/** CSS colour for a `SoftChip` (matches the `STATUS_COLOR` pattern of the plugins page). */
export function trustColor(level: TrustLevel): string {
  return TRUST_COLOR[level];
}

/** Where an installed plugin came from: the image, an upload, or a named repository. */
export function sourceLabel(t: TFunction, p: PluginAdmin): string {
  if (p.source === "repository") {
    return p.repository_name
      ? t("admin.plugins.source.repositoryNamed", { name: p.repository_name })
      : t("admin.plugins.source.repository");
  }
  if (p.source === "upload") return t("admin.plugins.source.upload");
  return t("admin.plugins.source.image");
}
