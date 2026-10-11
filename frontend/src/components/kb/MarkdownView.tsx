import { useMemo } from "react";
import { marked } from "marked";
import DOMPurify, { type Config } from "dompurify";
import { cn } from "@/lib/cn";

export type MarkdownViewProps = {
  markdown: string;
  className?: string;
  "data-testid"?: string;
  /** `restricted`: for text one person writes for many (shared dashboard notes) — HTML only
   * (no SVG or MathML), no forms, images, frames, embeds or inline styles; every link opens in a
   * new tab without referrer. */
  profile?: "default" | "restricted";
};

const RESTRICTED: Config = {
  // HTML only: SVG (<image href>, <feImage>) and MathML (xlink:href) could load remote URLs.
  USE_PROFILES: { html: true },
  FORBID_TAGS: [
    "form",
    "input",
    "button",
    "select",
    "option",
    "textarea",
    "img",
    "picture",
    "source",
    "video",
    "audio",
    "iframe",
    "frame",
    "object",
    "embed",
    "style",
    "link",
    "meta",
    "base",
  ],
  FORBID_ATTR: ["style", "srcset", "action", "formaction", "background", "poster"],
};

/** Sanitised HTML of `raw`; the restricted profile also forces safe link targets. */
function sanitize(raw: string, profile: "default" | "restricted"): string {
  if (profile === "default") return DOMPurify.sanitize(raw);
  const clean = DOMPurify.sanitize(raw, RESTRICTED);
  const tpl = document.createElement("template");
  tpl.innerHTML = clean;
  for (const a of tpl.content.querySelectorAll("a[href]")) {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  }
  return tpl.innerHTML;
}

/**
 * Renders KB article markdown as sanitised HTML.
 * Shared by the customer portal and agent KB reader/editor preview.
 */
export function MarkdownView({
  markdown,
  className,
  "data-testid": testId,
  profile = "default",
}: MarkdownViewProps) {
  const html = useMemo(() => {
    const raw = marked.parse(markdown, { async: false }) as string;
    return sanitize(raw, profile);
  }, [markdown, profile]);

  return (
    <div
      className={cn("prose-portal max-w-none text-sm text-ink", className)}
      data-testid={testId}
      // markdown is sanitised via DOMPurify before insertion.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
