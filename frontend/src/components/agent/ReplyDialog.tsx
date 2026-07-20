import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

/**
 * Modal reply composer for a single article. Prefilled from the backend
 * reply-draft endpoint: the answer area sits ABOVE the quoted original, and a
 * template dropdown inserts the queue's response templates at the top of the
 * answer. The outgoing article is created via the existing add_article path.
 */
export function ReplyDialog({
  ticketId,
  articleId,
  replyAll,
  open,
  onClose,
}: {
  ticketId: number;
  articleId: number;
  replyAll: boolean;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState("");
  // The answer (above the quote) and the quoted original are kept separate so
  // the agent types above the quote; they are joined on send.
  const [answer, setAnswer] = useState("");
  const [quote, setQuote] = useState("");
  const [templateId, setTemplateId] = useState("");

  const draftQ = useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "reply-draft", replyAll],
    queryFn: () => api.getReplyDraft(ticketId, articleId, replyAll),
    enabled: open,
  });

  const templatesQ = useQuery({
    queryKey: ["tickets", ticketId, "templates"],
    queryFn: () => api.listTemplates(ticketId),
    enabled: open,
  });

  // Seed the fields once the draft arrives.
  useEffect(() => {
    const d = draftQ.data;
    if (!d) return;
    setTo(d.to_address ?? "");
    setCc(d.cc ?? "");
    setSubject(d.subject);
    setAnswer("");
    setQuote(d.body);
  }, [draftQ.data]);

  const templates = templatesQ.data ?? [];

  const sendMutation = useMutation({
    mutationFn: () =>
      api.createArticle(ticketId, {
        sender_type: "agent",
        subject,
        // Answer above the quote, matching Znuny's reply layout.
        body: `${answer}\n${quote}`,
        content_type: "text/plain; charset=utf-8",
        channel: "email",
        is_visible_for_customer: true,
        to_address: to || null,
        cc: cc || null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["tickets", ticketId, "articles"],
      });
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      onClose();
    },
  });

  const onPickTemplate = (id: string) => {
    setTemplateId(id);
    const tpl = templates.find((x) => String(x.id) === id);
    if (tpl) setAnswer((prev) => (prev ? `${tpl.text}\n${prev}` : tpl.text));
  };

  const title = useMemo(
    () => (replyAll ? t("ticket.replyAll") : t("ticket.replyDialogTitle")),
    [replyAll, t],
  );

  return (
    <Dialog open={open} onClose={onClose} title={title} className="max-w-2xl">
      {draftQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : draftQ.isError ? (
        <p className="text-sm text-danger">{t("ticket.replyDraftError")}</p>
      ) : (
        <div className="space-y-2" data-testid="reply-dialog">
          <label className="block text-xs text-muted">
            {t("ticket.replyTo")}
            <input className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
          <label className="block text-xs text-muted">
            {t("ticket.replyCc")}
            <input className={inputCls} value={cc} onChange={(e) => setCc(e.target.value)} />
          </label>
          <label className="block text-xs text-muted">
            {t("ticket.replySubject")}
            <input
              className={inputCls}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          </label>
          {templates.length > 0 && (
            <label className="block text-xs text-muted">
              {t("ticket.replyTemplate")}
              <select
                className={inputCls}
                value={templateId}
                data-testid="reply-template-select"
                onChange={(e) => onPickTemplate(e.target.value)}
              >
                <option value="">{t("ticket.replyTemplateNone")}</option>
                {templates.map((tpl) => (
                  <option key={tpl.id} value={tpl.id}>
                    {tpl.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="block text-xs text-muted">
            {t("ticket.replyAnswer")}
            <textarea
              className={inputCls}
              rows={5}
              value={answer}
              data-testid="reply-answer"
              onChange={(e) => setAnswer(e.target.value)}
            />
          </label>
          <textarea
            className={`${inputCls} font-mono text-xs text-muted`}
            rows={6}
            value={quote}
            data-testid="reply-quote"
            onChange={(e) => setQuote(e.target.value)}
          />
          {sendMutation.isError && (
            <p className="text-xs text-danger">{t("ticket.replyError")}</p>
          )}
          <div className="flex items-center justify-end gap-1.5 pt-1">
            <Button variant="ghost" size="sm" onClick={onClose}>
              {t("ticket.composerCancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              data-testid="reply-send"
              disabled={!answer.trim() || sendMutation.isPending}
              onClick={() => sendMutation.mutate()}
            >
              {sendMutation.isPending ? t("ticket.replySending") : t("ticket.composerSend")}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
