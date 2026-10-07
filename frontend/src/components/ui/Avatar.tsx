import { useCallback, useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { domainLogoUrl, emailDomain, personAvatarUrl } from "@/lib/avatar";

export type AvatarProps = {
  /** Explicit picture URL (e.g. OIDC/Google ``picture`` claim) — tried first. */
  avatarUrl?: string | null;
  /** Email of the person: their picture via the avatar proxy, then (with
   * ``domainFallback``) the logo of the mail domain. */
  email?: string | null;
  /** Logo domain tried after the person picture (company website), e.g. for
   * company avatars that have no e-mail at all. */
  domain?: string | null;
  /** Try the logo of the e-mail's domain when there is no person picture
   * (default on). Lists turn it off — a logo in every row is noisy. */
  domainFallback?: boolean;
  /** Cache buster after an upload (appended as ``?v=`` to proxy URLs). */
  version?: string | null;
  /** Initials shown when there is no image source, or every image fails / 404s. */
  initials: string;
  /** CSS pixel size of the circle. */
  size?: number;
  className?: string;
  /** Optional test id for the root element. */
  testId?: string;
  /** Background/text tint used only by the initials fallback — "customer"
   * for a green (ok) tint, "accent" (default) for the usual accent tint. */
  tone?: "accent" | "customer";
};

type Candidate = { src: string; kind: "explicit" | "person" | "domain" };

/**
 * Circular avatar. Candidates in order: explicit ``avatarUrl`` → person
 * picture (``/api/v1/avatars/email/{sha256}``) → domain logo
 * (``/api/v1/avatars/domain/{domain}``) → initials. A load error (the proxy
 * answers 404 when it has nothing) moves on to the next candidate without a
 * broken image. Logos are shown whole on white instead of cropped.
 */
export function Avatar({
  avatarUrl,
  email,
  domain,
  domainFallback = true,
  version,
  initials,
  size = 24,
  className,
  testId,
  tone = "accent",
}: AvatarProps) {
  const candidates = useMemo(() => {
    const out: Candidate[] = [];
    const explicit = (avatarUrl ?? "").trim();
    if (explicit) out.push({ src: explicit, kind: "explicit" });
    const person = personAvatarUrl(email, version);
    if (person) out.push({ src: person, kind: "person" });
    const logo = domainLogoUrl(domain ?? (domainFallback ? emailDomain(email) : null), version);
    if (logo) out.push({ src: logo, kind: "domain" });
    return out;
  }, [avatarUrl, email, domain, domainFallback, version]);

  // Failures are remembered per source URL rather than as an index, so they
  // reset by themselves whenever the sources change — no reset effect that
  // could clobber a synchronous "already broken" detection on the same commit.
  const [failed, setFailed] = useState<ReadonlySet<string>>(() => new Set());
  const current = candidates.find((c) => !failed.has(c.src)) ?? null;
  const currentSrc = current?.src ?? "";

  const markFailed = useCallback(
    (src: string) =>
      setFailed((prev) => {
        if (prev.has(src)) return prev;
        const next = new Set(prev);
        next.add(src);
        return next;
      }),
    [],
  );

  // A cached failed image (e.g. a 404 hit again after client-side nav) can
  // fire its `error` event before React attaches `onError`, leaving a broken
  // image with no fallback. Re-check the element's load result the moment it
  // mounts and move on if it's broken.
  const checkBroken = useCallback(
    (node: HTMLImageElement | null) => {
      if (node?.complete && node.naturalWidth === 0) markFailed(currentSrc);
    },
    [currentSrc, markFailed],
  );

  const dim = { width: size, height: size };
  const base = cn(
    "inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full",
    className,
  );

  if (current) {
    const logo = current.kind === "domain";
    return (
      <img
        // A fresh element per candidate: no stale `complete`/`naturalWidth`
        // from the previous source when checkBroken runs.
        key={current.src}
        ref={checkBroken}
        src={current.src}
        alt=""
        width={size}
        height={size}
        data-testid={testId ?? "avatar-image"}
        data-kind={current.kind}
        className={cn(
          base,
          logo
            ? "border border-hairline bg-white object-contain"
            : "bg-surface-subtle object-cover",
        )}
        style={logo ? { ...dim, padding: Math.max(1, Math.round(size * 0.12)) } : dim}
        onError={() => markFailed(current.src)}
        referrerPolicy="no-referrer"
      />
    );
  }

  return (
    <span
      data-testid={testId ?? "avatar-initials"}
      className={cn(
        base,
        "flex-shrink-0 border border-hairline font-bold",
        tone === "customer" ? "bg-green/15 text-green" : "bg-accent-dim text-accent",
      )}
      style={{ ...dim, fontSize: size * 0.42 }}
      aria-hidden
    >
      {initials}
    </span>
  );
}
