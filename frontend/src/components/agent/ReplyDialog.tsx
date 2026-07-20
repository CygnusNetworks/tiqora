import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import {
  RecipientsField,
  joinRecipients,
  moveRecipientBetween,
  parseRecipientList,
  type Recipient,
} from "./RecipientsField";

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

type Field = "to" | "cc" | "bcc";

/**
 * Modal reply composer for a single article. Prefilled from the backend
 * reply-draft endpoint. Recipients are edited as Apple-Mail-style chips (To
 * always visible; Cc/Bcc/Reply-To are true toggles — expand/collapse, with a
 * count badge when collapsed and non-empty). Addresses stay in state when
 * collapsed and are still sent. The answer and quoted original share a SINGLE
 * editable body — the agent types above the quote in one field. The outgoing
 * article is created via the existing add_article path.
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
  // Cc/Bcc/Reply-To start collapsed when empty; draft seed may open Cc.
  const [showCc, setShowCc] = useState(false);
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

  // Move one recipient between To/Cc/Bcc (drag-drop). Removes from source and
  // appends to target (deduped by email) — never copies.
  const moveRecipient = (from: string, dest: string, r: Recipient) => {
    if (from === dest) return;
    const fromKey = from as Field;
    const destKey = dest as Field;
    if (!(fromKey in values) || !(destKey in values)) return;
    const { source, target } = moveRecipientBetween(
      values[fromKey],
      values[destKey],
      r,
    );
    setters[fromKey](source);
    setters[destKey](target);
    if (destKey === "cc") setShowCc(true);
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
    const nextTo = parseRecipientList(d.to_address);
    const nextCc = parseRecipientList(d.cc);
    setTo(nextTo);
    setCc(nextCc);
    setBcc([]);
    setReplyTo("");
    // Show Cc when the draft already has addresses; keep Bcc/Reply-To collapsed.
    setShowCc(nextCc.length > 0);
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
        // Threading headers from the reply-draft endpoint so the outbound
        // Message-ID chain matches Znuny follow-up detection.
        in_reply_to: draftQ.data?.in_reply_to ?? null,
        references: draftQ.data?.references ?? null,
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

  const canSend = body.trim().length > 0 && to.length > 0 && !sendMutation.isPending;

  // Wide on large viewports (~80+ mono chars in the body); full-width on mobile.
  // Dialog base is max-w-md; this className overrides via cn().
  const dialogWidth =
    "w-full max-w-full sm:max-w-2xl md:max-w-3xl lg:max-w-4xl xl:max-w-5xl";

  const toggleCls =
    "inline-flex items-center gap-1 rounded border border-hairline px-2 py-0.5 text-muted hover:text-ink";
  const toggleActiveCls = "border-accent/40 text-ink";
  // Compact count pill (QueuesPage open-badge style) — only when collapsed + non-empty.
  const countBadgeCls =
    "rounded-full bg-accent-dim px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-accent";

  const replyToCount = replyTo.trim() ? 1 : 0;

  return (
    <Dialog open={open} onClose={onClose} title={title} className={dialogWidth}>
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
            required
            placeholder={t("ticket.recipientAddHint")}
            testid="reply-to"
          />
          {showCc && (
            <RecipientsField
              label={t("ticket.replyCc")}
              fieldKey="cc"
              recipients={cc}
              onChange={setCc}
              onMove={moveRecipient}
              placeholder={t("ticket.recipientAddHint")}
              testid="reply-cc"
            />
          )}
          {showBcc && (
            <RecipientsField
              label={t("ticket.replyBcc")}
              fieldKey="bcc"
              recipients={bcc}
              onChange={setBcc}
              onMove={moveRecipient}
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
          {/* Always-visible true toggles: expand/collapse; badge when collapsed + count. */}
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <button
              type="button"
              className={cn(toggleCls, showCc && toggleActiveCls)}
              data-testid="reply-toggle-cc"
              aria-expanded={showCc}
              onClick={() => setShowCc((v) => !v)}
            >
              {t("ticket.replyCc")}
              {!showCc && cc.length > 0 && (
                <span className={countBadgeCls} data-testid="reply-toggle-cc-count">
                  {cc.length}
                </span>
              )}
            </button>
            <button
              type="button"
              className={cn(toggleCls, showBcc && toggleActiveCls)}
              data-testid="reply-toggle-bcc"
              aria-expanded={showBcc}
              onClick={() => setShowBcc((v) => !v)}
            >
              {t("ticket.replyBcc")}
              {!showBcc && bcc.length > 0 && (
                <span className={countBadgeCls} data-testid="reply-toggle-bcc-count">
                  {bcc.length}
                </span>
              )}
            </button>
            <button
              type="button"
              className={cn(toggleCls, showReplyTo && toggleActiveCls)}
              data-testid="reply-toggle-replyto"
              aria-expanded={showReplyTo}
              onClick={() => setShowReplyTo((v) => !v)}
            >
              {t("ticket.replyReplyTo")}
              {!showReplyTo && replyToCount > 0 && (
                <span
                  className={countBadgeCls}
                  data-testid="reply-toggle-replyto-count"
                >
                  {replyToCount}
                </span>
              )}
            </button>
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
              disabled={!canSend}
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
