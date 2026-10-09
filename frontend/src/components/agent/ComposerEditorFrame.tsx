import { useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { ArticleBodyRenderer } from "./ArticleBodyRenderer";
import { ComposerTimeChip } from "./ComposerTimeChip";
import { AttachmentChips } from "./telegram/ComposerChips";
import {
  MAX_ATTACHMENTS,
  MAX_ATTACHMENT_BYTES,
  formatBytes,
  type ChatAttachments,
} from "./telegram/useChatAttachments";

/**
 * The framed mail editor shared by the reply dialog and the new email
 * ticket: the text (passed as `children`), the signature the backend will
 * append, the attached files, and a toolbar along the bottom edge (refine,
 * "Datei", time booking). Files can also be dropped onto the frame.
 *
 * Test ids derive from `testIdPrefix`: `-editor`, `-signature-preview`,
 * `-signature-plain`, `-attachments`, `-attach`, `-attach-input`, `-time`,
 * `-attach-error`.
 */
export function ComposerEditorFrame({
  children,
  signature,
  refine,
  attachments,
  timeUnits,
  onTimeUnitsChange,
  locked = false,
  testIdPrefix,
}: {
  children: ReactNode;
  /** Read-only preview — never part of the editable body, or it would be
   * appended twice on send. */
  signature?: { text: string; isHtml: boolean } | null;
  /** Left end of the toolbar, usually `<RefineControls variant="toolbar">`. */
  refine?: ReactNode;
  attachments: ChatAttachments;
  timeUnits: string;
  onTimeUnitsChange: (value: string) => void;
  /** After the message went out: no more files in or out. */
  locked?: boolean;
  testIdPrefix: string;
}) {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  // Defensive like the former per-page previews: an older backend (or the
  // demo) may send a compose context without a signature at all.
  const hasSignature = Boolean(signature?.text?.trim());

  return (
    <>
      <div
        className={cn(
          "overflow-hidden rounded-lg border border-hairline bg-surface-subtle/40 focus-within:border-accent/60",
          dragOver && "border-accent ring-1 ring-accent",
        )}
        data-testid={`${testIdPrefix}-editor`}
        onDragOver={(e) => {
          if (locked || !e.dataTransfer.types.includes("Files")) return;
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(false);
        }}
        onDrop={(e) => {
          if (locked || e.dataTransfer.files.length === 0) return;
          e.preventDefault();
          setDragOver(false);
          attachments.add(e.dataTransfer.files);
        }}
      >
        {children}
        {signature && hasSignature && (
          <div
            className="mx-3 mb-2.5 border-t border-dashed border-hairline pt-2"
            data-testid={`${testIdPrefix}-signature-preview`}
          >
            <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted">
              {t("ticket.signatureAppended")}
            </p>
            {signature.isHtml ? (
              <ArticleBodyRenderer body={signature.text} isHtml className="text-xs text-muted" />
            ) : (
              <pre
                className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-muted"
                data-testid={`${testIdPrefix}-signature-plain`}
              >
                {signature.text}
              </pre>
            )}
          </div>
        )}
        {attachments.items.length > 0 && (
          <div
            className="flex flex-wrap gap-1.5 border-t border-hairline px-3 py-2"
            data-testid={`${testIdPrefix}-attachments`}
          >
            <AttachmentChips
              items={attachments.items}
              onRemove={locked ? () => undefined : attachments.remove}
            />
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 border-t border-hairline bg-surface px-2 py-1.5">
          {refine}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid={`${testIdPrefix}-attach`}
            disabled={locked}
            title={t("ticket.telegram.composer.attach")}
            onClick={() => fileRef.current?.click()}
          >
            <span aria-hidden>📎</span> {t("newTicket.attachFile")}
          </Button>
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            data-testid={`${testIdPrefix}-attach-input`}
            onChange={(e) => {
              if (e.target.files) attachments.add(e.target.files);
              // Same file again must fire onChange after a removal.
              e.target.value = "";
            }}
          />
          <span className="ml-auto">
            <ComposerTimeChip
              value={timeUnits}
              onChange={onTimeUnitsChange}
              testId={`${testIdPrefix}-time`}
            />
          </span>
        </div>
      </div>
      {attachments.error && (
        <p className="text-xs text-danger" data-testid={`${testIdPrefix}-attach-error`}>
          {attachments.error === "tooLarge"
            ? t("ticket.telegram.composer.attachTooLarge", { max: formatBytes(MAX_ATTACHMENT_BYTES) })
            : attachments.error === "tooMany"
              ? t("ticket.telegram.composer.attachTooMany", { max: MAX_ATTACHMENTS })
              : t("ticket.telegram.composer.attachReadFailed")}
        </p>
      )}
    </>
  );
}
