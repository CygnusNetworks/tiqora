import { useMemo } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { cn } from "@/lib/cn";

export type MarkdownViewProps = {
  markdown: string;
  className?: string;
  "data-testid"?: string;
};

/**
 * Renders KB article markdown as sanitised HTML.
 * Shared by the customer portal and agent KB reader/editor preview.
 */
export function MarkdownView({
  markdown,
  className,
  "data-testid": testId,
}: MarkdownViewProps) {
  const html = useMemo(() => {
    const raw = marked.parse(markdown, { async: false }) as string;
    return DOMPurify.sanitize(raw);
  }, [markdown]);

  return (
    <div
      className={cn("prose-portal max-w-none text-sm text-ink", className)}
      data-testid={testId}
      // markdown is sanitised via DOMPurify before insertion.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
