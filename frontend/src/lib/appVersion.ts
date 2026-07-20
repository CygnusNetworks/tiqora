/**
 * Build provenance for display in the UI.
 *
 * Values are injected at build time via Vite env (see Dockerfile build args,
 * wired from the CI git ref/sha). In local dev they are unset, so we show
 * "dev". `label` prefers a release tag (e.g. "v1.2.0"); otherwise it falls
 * back to the short commit sha. `sha` is the full commit for the tooltip.
 */
const rawVersion = (import.meta.env.VITE_APP_VERSION ?? "").trim();
const rawSha = (import.meta.env.VITE_GIT_SHA ?? "").trim();

export const appVersion = {
  /** Full commit sha the build was produced from (empty in dev). */
  sha: rawSha,
  /** Short, human-facing label: tag if set, else short sha, else "dev". */
  label: rawVersion
    ? rawVersion.startsWith("v")
      ? rawVersion
      : `v${rawVersion}`
    : rawSha
      ? rawSha.slice(0, 7)
      : "dev",
};
