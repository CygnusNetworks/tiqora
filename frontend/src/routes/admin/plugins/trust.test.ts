import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { adminPluginRow } from "@/test/pluginFixtures";
import { sourceLabel, trustColor, trustLabelKey, type TrustLevel } from "./trust";

const t = ((key: string, o?: { name?: string }) => (o?.name ? `${key}:${o.name}` : key)) as unknown as TFunction;

describe("trust", () => {
  it("maps trust levels to labels and CSS colours", () => {
    expect(trustColor("invalid")).toBe("var(--color-danger)");
    expect(trustLabelKey("verified_publisher")).toBe("admin.plugins.trust.verified_publisher");
    const levels: TrustLevel[] = ["official", "verified_publisher", "local", "unsigned", "invalid"];
    expect(new Set(levels.map(trustColor)).size).toBe(5);
    expect(trustColor("official")).toBe("var(--color-green)");
    expect(trustColor("unsigned")).toBe("var(--color-warn)");
  });

  it("names the source of an installed plugin", () => {
    expect(sourceLabel(t, adminPluginRow({ source: "image" }))).toBe("admin.plugins.source.image");
    expect(sourceLabel(t, adminPluginRow({ source: "upload" }))).toBe("admin.plugins.source.upload");
    expect(sourceLabel(t, adminPluginRow({ source: "repository" }))).toBe("admin.plugins.source.repository");
    const update = { image_plugin: false, repository_id: 3, repository_name: "Alt", version: "2.0.0" };
    expect(sourceLabel(t, adminPluginRow({ source: "repository", update }))).toBe(
      "admin.plugins.source.repositoryNamed:Alt",
    );
    expect(sourceLabel(t, adminPluginRow({ source: "repository", update }), new Map([[3, "Neu"]]))).toBe(
      "admin.plugins.source.repositoryNamed:Neu",
    );
  });
});
