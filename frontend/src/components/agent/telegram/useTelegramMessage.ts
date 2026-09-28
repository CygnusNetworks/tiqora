import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { decodeEntities, stripHtml } from "@/lib/html";
import { telegramChatKey } from "@/lib/telegramChatApi";
import { isUncertainFailure, readableApiReason } from "./telegramErrors";

/** Plain text of an article body, shared with the bubble's own body query
 * (same key) so quoting an already-rendered message costs no request. */
export function useArticlePlainText(ticketId: number, articleId: number, enabled = true) {
  const q = useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "body"],
    queryFn: () => api.getArticleBody(ticketId, articleId),
    enabled,
  });
  const plain = q.data ? (q.data.is_html ? stripHtml(q.data.body) : decodeEntities(q.data.body)) : null;
  return { plain, isLoading: q.isLoading };
}

/**
 * Edit/retract/button-removal state for one Telegram bubble. The mode lives here rather than
 * in the hover island because the island hides on mouse-out, while the editor
 * and the confirmation must stay put.
 */
export function useTelegramMessageActions(ticketId: number, articleId: number) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [mode, setMode] = useState<"idle" | "editing" | "confirmRetract">("idle");

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "articles"] });
    void qc.invalidateQueries({ queryKey: telegramChatKey(ticketId) });
  };
  // After an unclear failure the change may have gone through — refetch.
  const refreshIfUncertain = (err: unknown) => {
    if (isUncertainFailure(err)) refresh();
  };
  const edit = useMutation({
    mutationFn: (body: string) => api.editTelegramMessage(ticketId, articleId, body),
    onSuccess: () => {
      setMode("idle");
      refresh();
    },
    onError: refreshIfUncertain,
  });
  const retract = useMutation({
    mutationFn: () => api.retractTelegramMessage(ticketId, articleId),
    onSuccess: () => {
      setMode("idle");
      refresh();
    },
    onError: refreshIfUncertain,
  });

  const removeButtons = useMutation({
    mutationFn: () => api.removeTelegramButtons(ticketId, articleId),
    onSuccess: refresh,
    onError: refreshIfUncertain,
  });

  const failure = edit.error ?? retract.error ?? removeButtons.error;
  // A 409 carries Telegram's refusal ("older than 48 h", "already deleted") —
  // that reason is what the agent needs; anything else (validation list,
  // proxy page, 5xx) gets the generic line.
  const error = failure ? (readableApiReason(failure) ?? t("ticket.telegram.actionError")) : null;

  const start = (next: typeof mode) => {
    edit.reset();
    retract.reset();
    removeButtons.reset();
    setMode(next);
  };

  return {
    mode,
    startEdit: () => start("editing"),
    startRetract: () => start("confirmRetract"),
    cancel: () => start("idle"),
    save: (body: string) => edit.mutate(body),
    confirmRetract: () => retract.mutate(),
    removeButtons: () => {
      edit.reset();
      retract.reset();
      removeButtons.mutate();
    },
    pending: edit.isPending || retract.isPending || removeButtons.isPending,
    error,
  };
}
