import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError, api } from "@/lib/api";
import { useConfirm } from "@/components/ui/ConfirmDialog";

/**
 * Confirm + delete flow for one internal note, shared by `ArticleQuickActions`
 * (split view reader / conversation bubble hover menu) and `NotePill`
 * (conversation view's dedicated internal-note rendering). Invalidates the
 * same article/ticket queries the composer's send mutation does.
 */
export function useDeleteArticleNote(ticketId: number, articleId: number) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { confirm, dialog } = useConfirm();

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteArticle(ticketId, articleId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId, "articles"] });
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId, "history"] });
    },
  });

  const errorMessage = !deleteMutation.isError
    ? null
    : deleteMutation.error instanceof ApiError && deleteMutation.error.status === 409
      ? t("ticket.deleteNoteErrorNotInternal")
      : t("ticket.deleteNoteError");

  const requestDelete = async () => {
    const ok = await confirm({
      title: t("ticket.deleteNote"),
      message: t("ticket.deleteNoteConfirm"),
      confirmLabel: t("ticket.deleteNoteConfirmButton"),
      variant: "danger",
    });
    if (!ok) return;
    deleteMutation.mutate();
  };

  return {
    requestDelete,
    isPending: deleteMutation.isPending,
    errorMessage,
    dialog,
  };
}
