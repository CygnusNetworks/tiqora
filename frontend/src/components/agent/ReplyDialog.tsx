import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import {
  RecipientsField,
  joinRecipients,
  parseRecipientList,
  type Recipient,
} from "./RecipientsField";

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

type Field = "to" | "cc" | "bcc";

/**
 * Modal reply composer for a single article. Prefilled from the backend
 * reply-draft endpoint. Recipients are edited as Apple-Mail-style chips (To/Cc,
 * with optional Bcc + Reply-To), and the answer and quoted original share a
 * SINGLE editable body — the agent types above the quote in one field. The
 * outgoing article is created via the existing add_article path.
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

  const [to, setTo] = useState<Recipient[]>([]);
  const [cc, setCc] = useState<Recipient[]>([]);
  const [bcc, setBcc] = useState<Recipient[]>([]);
  const [replyTo, setReplyTo] = useState("");
  const [showBcc, setShowBcc] = useState(false);
  const [showReplyTo, setShowReplyTo] = useState(false);
  const [subject, setSubject] = useState("");
  // Answer and quoted original share ONE editable field; the quote is seeded
  // below an empty answer area and the agent edits the whole thing inline.
  const [body, setBody] = useState("");
  const [templateId, setTemplateId] = useState("");

  const setters: Record<Field, (r: Recipient[]) => void> = {
    to: setTo,
    cc: setCc,
    bcc: setBcc,
  };
  const values: Record<Field, Recipient[]> = { to, cc, bcc };

  // Move one recipient between the To/Cc/Bcc fields (drag-drop or the explicit
  // "→ Cc" action both route through here).
  const moveRecipient = (from: string, dest: string, r: Recipient) => {
    const fromKey = from as Field;
    const destKey = dest as Field;
    setters[fromKey](values[fromKey].filter((x) => x !== r));
    setters[destKey]([...values[destKey], r]);
    if (destKey === "bcc") setShowBcc(true);
  };

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
    setTo(parseRecipientList(d.to_address));
    setCc(parseRecipientList(d.cc));
    setBcc([]);
    setReplyTo("");
    setShowBcc(false);
    setShowReplyTo(false);
    setSubject(d.subject);
    // Empty answer on top, blank line, then the quoted original.
    setBody(`\n\n${d.body}`);
  }, [draftQ.data]);

  const templates = templatesQ.data ?? [];

  const sendMutation = useMutation({
    mutationFn: () =>
      api.createArticle(ticketId, {
        sender_type: "agent",
        subject,
        body,
        content_type: "text/plain; charset=utf-8",
        channel: "email",
        is_visible_for_customer: true,
        to_address: joinRecipients(to),
        cc: joinRecipients(cc),
        bcc: joinRecipients(bcc),
        reply_to: replyTo.trim() || null,
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
    if (tpl) setBody((prev) => `${tpl.text}\n${prev}`);
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
          <RecipientsField
            label={t("ticket.replyTo")}
            fieldKey="to"
            recipients={to}
            onChange={setTo}
            onMove={moveRecipient}
            moveTargets={[{ key: "cc", label: t("ticket.replyCc") }]}
            placeholder={t("ticket.recipientAddHint")}
            testid="reply-to"
          />
          <RecipientsField
            label={t("ticket.replyCc")}
            fieldKey="cc"
            recipients={cc}
            onChange={setCc}
            onMove={moveRecipient}
            moveTargets={[{ key: "to", label: t("ticket.replyTo") }]}
            placeholder={t("ticket.recipientAddHint")}
            testid="reply-cc"
          />
          {showBcc && (
            <RecipientsField
              label={t("ticket.replyBcc")}
              fieldKey="bcc"
              recipients={bcc}
              onChange={setBcc}
              onMove={moveRecipient}
              moveTargets={[
                { key: "to", label: t("ticket.replyTo") },
                { key: "cc", label: t("ticket.replyCc") },
              ]}
              placeholder={t("ticket.recipientAddHint")}
              testid="reply-bcc"
            />
          )}
          {showReplyTo && (
            <label className="block text-xs text-muted">
              {t("ticket.replyReplyTo")}
              <input
                className={inputCls}
                value={replyTo}
                data-testid="reply-replyto"
                placeholder={t("ticket.recipientAddHint")}
                onChange={(e) => setReplyTo(e.target.value)}
              />
            </label>
          )}
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            {!showBcc && (
              <button
                type="button"
                className="rounded border border-hairline px-2 py-0.5 text-muted hover:text-ink"
                data-testid="reply-toggle-bcc"
                onClick={() => setShowBcc(true)}
              >
                {t("ticket.replyBcc")}
              </button>
            )}
            {!showReplyTo && (
              <button
                type="button"
                className="rounded border border-hairline px-2 py-0.5 text-muted hover:text-ink"
                data-testid="reply-toggle-replyto"
                onClick={() => setShowReplyTo(true)}
              >
                {t("ticket.replyReplyTo")}
              </button>
            )}
          </div>
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
              className={`${inputCls} font-mono text-xs`}
              rows={12}
              value={body}
              data-testid="reply-body"
              onChange={(e) => setBody(e.target.value)}
            />
          </label>
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
              disabled={!body.trim() || to.length === 0 || sendMutation.isPending}
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
