import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { formatBytes } from "@/lib/format";

/**
 * Attachment list + single-file upload + per-row delete for a KB article.
 * Only meaningful once the article exists, so this is rendered on the edit
 * page. Mirrors the hidden-input + ref upload pattern used in the portal
 * ticket detail page.
 */
export function KbAttachments({ articleId }: { articleId: number }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const listQ = useQuery({
    queryKey: ["kb", "attachments", articleId],
    queryFn: ({ signal }) => api.listKbAttachments(articleId, signal),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["kb", "attachments", articleId] });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadKbAttachment(articleId, file),
    onSuccess: () => void invalidate(),
  });

  const deleteMutation = useMutation({
    mutationFn: (attachmentId: number) => api.deleteKbAttachment(articleId, attachmentId),
    onSuccess: () => void invalidate(),
  });

  const onPickFile = () => fileInputRef.current?.click();

  const onFileChosen = async () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    try {
      await uploadMutation.mutateAsync(file);
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const attachments = listQ.data ?? [];
  const uploadError =
    uploadMutation.error instanceof ApiError
      ? uploadMutation.error.status === 413
        ? t("kb.attachments.tooLarge")
        : t("kb.attachments.uploadFailed")
      : null;

  return (
    <section
      className="rounded-lg border border-hairline bg-surface p-4"
      data-testid="kb-attachments"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">{t("kb.attachments.title")}</h2>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={onPickFile}
          disabled={uploadMutation.isPending}
          data-testid="kb-attachment-upload-btn"
        >
          {uploadMutation.isPending ? <Spinner /> : t("kb.attachments.upload")}
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          data-testid="kb-attachment-input"
          onChange={() => void onFileChosen()}
        />
      </div>

      {listQ.isLoading ? (
        <div className="flex justify-center py-3">
          <Spinner />
        </div>
      ) : attachments.length === 0 ? (
        <p className="py-2 text-sm text-muted" data-testid="kb-attachments-empty">
          {t("kb.attachments.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline" data-testid="kb-attachments-list">
          {attachments.map((att) => (
            <li
              key={att.id}
              className="flex items-center gap-3 py-2 text-sm"
              data-testid={`kb-attachment-${att.id}`}
            >
              <a
                href={api.kbAttachmentDownloadUrl(articleId, att.id)}
                className="min-w-0 flex-1 truncate text-accent hover:underline"
                title={att.filename}
              >
                {att.filename}
              </a>
              <span className="shrink-0 font-mono text-xs tabular-nums text-muted">
                {formatBytes(att.size)}
              </span>
              <button
                type="button"
                data-testid={`kb-attachment-delete-${att.id}`}
                onClick={() => deleteMutation.mutate(att.id)}
                disabled={deleteMutation.isPending}
                className="shrink-0 text-xs text-danger hover:underline disabled:opacity-50"
              >
                {t("kb.attachments.delete")}
              </button>
            </li>
          ))}
        </ul>
      )}

      {uploadError && (
        <p className="mt-2 text-sm text-danger" role="alert">
          {uploadError}
        </p>
      )}
    </section>
  );
}
