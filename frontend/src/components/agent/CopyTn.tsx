import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";

/**
 * Ticket number that copies itself to the clipboard on click (icon after it,
 * turning into a check for a moment). Sits inside clickable rows too, so it
 * swallows click/keydown instead of letting the row open the ticket.
 */
export function CopyTn({
  tn,
  className,
  iconOnHover = false,
  testId,
}: {
  tn: string;
  className?: string;
  /** Dense lists: show the icon only while the row/number is hovered or focused. */
  iconOnHover?: boolean;
  testId?: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(id);
  }, [copied]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(tn);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  const label = copied ? t("ticket.nav.tnCopied") : t("ticket.nav.copyTn");
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        void copy();
      }}
      onKeyDown={(e) => e.stopPropagation()}
      title={label}
      aria-label={`${label}: ${tn}`}
      className={cn(
        "group/copy inline-flex items-center gap-1 rounded font-mono tabular-nums transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
        className,
      )}
      data-testid={testId}
    >
      {tn}
      <svg
        viewBox="0 0 24 24"
        width="12"
        height="12"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className={cn(
          "flex-none",
          copied
            ? "text-accent"
            : iconOnHover
              ? "opacity-0 group-hover/copy:opacity-100 group-focus-visible/copy:opacity-100 [@media(hover:none)]:opacity-60"
              : "opacity-60 group-hover/copy:opacity-100",
        )}
      >
        {copied ? (
          <path d="M5 12l5 5L20 7" />
        ) : (
          <>
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15V6a2 2 0 012-2h9" />
          </>
        )}
      </svg>
    </button>
  );
}
