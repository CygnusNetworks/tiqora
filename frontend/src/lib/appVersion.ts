/**
 * Build provenance for display in the UI.
 *
 * Values are injected at build time via Vite env (see Dockerfile build args,
 * wired from CI). `VITE_APP_VERSION` carries `git describe --tags --long
 * --always`:
 *   - exactly on a tag  → "v1.4.0-0-g4520721"   → shown as "v1.4.0"
 *   - after a tag       → "v1.4.0-5-g4520721"   → shown as "v1.4.0 +5 · 4520721"
 *   - no tag reachable  → "4520721" (abbrev sha) → shown as-is
 *   - local dev (unset) → "dev"
 * `VITE_GIT_SHA` is the full commit sha, shown in the footer tooltip.
 */

const DESCRIBE_RE = /^(.+)-(\d+)-g([0-9a-f]+)$/i;

/** Turn the build-time describe string + full sha into the footer label. */
export function formatVersion(rawVersion: string, rawSha: string): string {
  const described = rawVersion.match(DESCRIBE_RE);
  if (described) {
    const [, tag, distanceStr, gsha] = described;
    const distance = Number(distanceStr);
    // Exactly on the tag: show the clean tag, nothing else.
    if (distance === 0) return tag;
    // Between tags: tag + how many commits ahead + the current commit id.
    const short = rawSha ? rawSha.slice(0, 7) : gsha;
    return `${tag} +${distance} · ${short}`;
  }
  // No reachable tag → describe already gave the abbreviated sha.
  if (rawVersion) return rawVersion;
  // Legacy fallback: only the full sha was provided.
  if (rawSha) return rawSha.slice(0, 7);
  return "dev";
}

const rawVersion = (import.meta.env.VITE_APP_VERSION ?? "").trim();
const rawSha = (import.meta.env.VITE_GIT_SHA ?? "").trim();

export const appVersion = {
  /** Full commit sha the build was produced from (empty in dev). */
  sha: rawSha,
  /** Short, human-facing label: tag, "tag +N · sha", abbrev sha, or "dev". */
  label: formatVersion(rawVersion, rawSha),
};
