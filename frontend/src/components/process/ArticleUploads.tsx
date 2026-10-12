import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { AttachmentChips } from "@/components/agent/telegram/ComposerChips";
import {
  MAX_ATTACHMENT_BYTES,
  MAX_ATTACHMENTS,
  formatBytes,
  type ChatAttachments,
} from "@/components/agent/telegram/useChatAttachments";
import { Button } from "@/components/ui/Button";

/**
 * Files for the article of a process dialog: chips with remove buttons and the same size and count
 * limits and messages as the reply dialog (`useChatAttachments`; the server takes the same 10 files).
 */
export function ArticleUploads({
  uploads,
  disabled,
  testId = "process-uploads",
}: {
  uploads: ChatAttachments;
  disabled?: boolean;
  testId?: string;
}) {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  return (
    <div className="mt-3 min-w-0" data-testid={testId}>
      <span className="mb-1 block text-xs font-medium text-muted">{t("process.field.ArticleAttachments")}</span>
      <div className="flex flex-wrap items-center gap-1.5">
        <AttachmentChips items={uploads.items} onRemove={disabled ? () => undefined : uploads.remove} />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          data-testid={`${testId}-add`}
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
          data-testid={`${testId}-input`}
          onChange={(e) => {
            if (e.target.files) uploads.add(e.target.files);
            // the same file again must fire onChange after a removal
            e.target.value = "";
          }}
        />
      </div>
      {uploads.error && (
        <p className="mt-1 text-xs text-danger" role="alert" data-testid={`${testId}-error`}>
          {uploads.error === "tooLarge"
            ? t("ticket.telegram.composer.attachTooLarge", { max: formatBytes(MAX_ATTACHMENT_BYTES) })
            : uploads.error === "tooMany"
              ? t("ticket.telegram.composer.attachTooMany", { max: MAX_ATTACHMENTS })
              : t("ticket.telegram.composer.attachReadFailed")}
        </p>
      )}
    </div>
  );
}
