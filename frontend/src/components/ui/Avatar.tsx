import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import { gravatarUrl } from "@/lib/gravatar";

export type AvatarProps = {
  /** Explicit picture URL (e.g. OIDC/Google ``picture`` claim) — preferred over Gravatar. */
  avatarUrl?: string | null;
  /** Email used for Gravatar lookup when no explicit avatar URL is set. */
  email?: string | null;
  /** Initials shown when there is no image source, or image load fails / 404s. */
  initials: string;
  /** CSS pixel size of the circle (image is requested at 2× for retina). */
  size?: number;
  className?: string;
  /** Optional test id for the root element. */
  testId?: string;
};

/**
 * Circular avatar priority: explicit ``avatarUrl`` → Gravatar(email) → initials.
 * Load errors (including Gravatar ``d=404``) fall back to initials without a
 * broken image.
 */
export function Avatar({
  avatarUrl,
  email,
  initials,
  size = 24,
  className,
  testId,
}: AvatarProps) {
  const [failed, setFailed] = useState(false);
  const explicit = (avatarUrl ?? "").trim() || null;
  const src = !failed
    ? (explicit ?? gravatarUrl(email, { size: size * 2 }))
    : null;

  useEffect(() => {
    setFailed(false);
  }, [avatarUrl, email]);

  const dim = { width: size, height: size };
  const base = cn(
    "inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full",
    className,
  );

  if (src) {
    return (
      <img
        src={src}
        alt=""
        width={size}
        height={size}
        data-testid={testId ?? "avatar-image"}
        className={cn(base, "bg-surface-subtle object-cover")}
        style={dim}
        onError={() => setFailed(true)}
        referrerPolicy="no-referrer"
      />
    );
  }

  return (
    <span
      data-testid={testId ?? "avatar-initials"}
      className={cn(
        base,
        "bg-accent font-bold text-accent-ink",
        size <= 24 ? "text-[10.5px]" : "text-[11px]",
      )}
      style={dim}
      aria-hidden
    >
      {initials}
    </span>
  );
}
