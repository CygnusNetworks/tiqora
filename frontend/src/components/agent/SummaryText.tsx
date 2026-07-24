import { Fragment, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";

/**
 * Renders an AI ticket summary (see `tiqora.ai.summary`) with its
 * "Dokumente:" section visually set apart and document filenames emphasised.
 *
 * The backend prompt guarantees the documents part is one or more paragraphs,
 * the first of which starts with the literal label `Dokumente:` and always
 * comes last. Everything from that paragraph onward is lifted into a distinct
 * card so an agent can tell at a glance where the conversation summary ends
 * and the document digests begin. Filenames (tokens ending in a known
 * attachment extension) are bolded wherever they appear.
 */

// Common attachment extensions produced by mail ingestion. Matches a
// space-free filename token ending in one of them — a filename with spaces is
// still highlighted from its last space on, which is good enough and never
// swallows a whole sentence.
const FILENAME_RE =
  /[\w()\-.]+\.(?:pdf|docx?|xlsx?|pptx?|csv|txt|rtf|odt|ods|odp|png|jpe?g|gif|tiff?|bmp|webp|heic|zip|rar|7z|eml|msg|xml|json|html?)/gi;

const DOCS_LABEL_RE = /^Dokumente:\s*/i;

function splitParagraphs(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
}

/** Bold every filename-looking token inside a run of text. */
function emphasiseFilenames(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  FILENAME_RE.lastIndex = 0;
  let i = 0;
  while ((match = FILENAME_RE.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    out.push(
      <span key={`${keyPrefix}-fn-${i}`} className="font-semibold text-ink">
        {match[0]}
      </span>,
    );
    last = match.index + match[0].length;
    i += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function SummaryText({
  body,
  className,
  testId,
}: {
  body: string;
  className?: string;
  testId?: string;
}) {
  const { t } = useTranslation();
  const paragraphs = splitParagraphs(body);
  const docsStart = paragraphs.findIndex((p) => DOCS_LABEL_RE.test(p));

  const convo = docsStart === -1 ? paragraphs : paragraphs.slice(0, docsStart);
  const docParagraphs = docsStart === -1 ? [] : paragraphs.slice(docsStart);

  return (
    <div className={cn("space-y-2", className)} data-testid={testId}>
      {convo.length > 0 && (
        <div className="space-y-2 whitespace-pre-wrap text-sm text-ink">
          {convo.map((p, idx) => (
            <p key={`c-${idx}`}>{p}</p>
          ))}
        </div>
      )}
      {docParagraphs.length > 0 && (
        <div
          className="space-y-1.5 rounded-md border border-hairline border-l-2 border-l-accent bg-surface-subtle px-3 py-2"
          data-testid={testId ? `${testId}-docs` : undefined}
        >
          <p className="text-[11px] font-semibold uppercase tracking-wide text-accent">
            {t("ticket.ai.documentsSection")}
          </p>
          <div className="space-y-1.5 whitespace-pre-wrap text-[13px] text-ink">
            {docParagraphs.map((p, idx) => {
              // Strip the "Dokumente:" label off the first paragraph — it is
              // replaced by the styled heading above.
              const text = idx === 0 ? p.replace(DOCS_LABEL_RE, "") : p;
              return (
                <p key={`d-${idx}`}>
                  {emphasiseFilenames(text, `d-${idx}`).map((node, ni) => (
                    <Fragment key={ni}>{node}</Fragment>
                  ))}
                </p>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
