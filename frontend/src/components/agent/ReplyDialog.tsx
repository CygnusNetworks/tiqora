import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { pluginPointsApi } from "@/lib/pluginPointsApi";
import { isPluginChannelName } from "@/lib/articleChannel";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { cn } from "@/lib/cn";
import {
  getDraft,
  useClearReplyDraft,
  useReplyDraft,
  useReplyDraftsLoaded,
  useSaveReplyDraft,
} from "@/lib/replyDrafts";
import { postComposerExtras } from "@/lib/composerExtras";
import type { PickedMention } from "@/lib/mentions";
import { useComposerLock } from "@/lib/composerLock";
import { ComposerLockBanner } from "./ComposerLock";
import { ComposerEditorFrame } from "./ComposerEditorFrame";
import { useChatAttachments } from "./telegram/useChatAttachments";
import { isInvalidTldAddress, useTicketChat } from "@/lib/webchatApi";
import { EmailSecurityControl } from "./EmailSecurityControl";
import { useEmailSecurity } from "./useEmailSecurity";
import { MentionTextarea } from "./MentionTextarea";
import { RefineControls } from "./RefineControls";
import { toneLabelKey } from "@/lib/refineTone";
import { RefineDiffView } from "./RefineDiffView";
import { useRefineReview } from "./useRefineReview";
import {
  defaultPendingDate,
  todayIso,
  needsDate,
  useNextStateOptions,
  type NextState,
} from "./replyNextState";
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
 *
 * Edits are mirrored into `@/lib/replyDrafts` (debounced) so closing without
 * sending leaves a visible, restorable draft rather than silently stashing
 * the text in component state: the article views render a placeholder for it
 * and the reply buttons switch to "continue draft". Sending clears it;
 * discarding is explicit and confirmed. Attached files are not part of that
 * draft — they live in this (still mounted) component until sent or discarded.
 */
export function ReplyDialog({
  ticketId,
  articleId,
  replyAll,
  open,
  onClose,
  initialDraft,
  channelName,
}: {
  ticketId: number;
  articleId: number;
  replyAll: boolean;
  open: boolean;
  onClose: () => void;
  /** Seeds the body from an AI draft (plan §3.4 "Als Antwort übernehmen").
   * The draft stays `open` on the backend until `ai_draft_id` round-trips
   * through the actual send below — closing without sending leaves it
   * untouched. */
  initialDraft?: { id: number; subject: string | null; body: string } | null;
  /** Channel name of the article being replied to (`channelNameOf(article)`
   * from `@/lib/articleChannel`) — routes the outgoing article. Only
   * "Telegram" changes behavior today: no addresses, delivered via the
   * Telegram gateway instead of email. */
  channelName?: string;
}) {
  const isTelegram = channelName === "Telegram";
  // Plugin channels (D18): the channel's limits come from `GET /channels/plugin`,
  // matched on the article's channel name. Core channels never ask.
  const { t } = useTranslation();
  // A `Plugin_…` article is answered on its plugin channel or not at all: never by
  // e-mail, even when the channel cannot be resolved (its handle may look like an address).
  const isPlugin = isPluginChannelName(channelName);
  const pluginChannelsQ = useQuery({
    queryKey: ["channels", "plugin"],
    queryFn: async ({ signal }) => (await pluginPointsApi.pluginChannels(signal)) ?? [],
    enabled: open && isPlugin,
    staleTime: 60_000,
  });
  const pluginChannel = isPlugin
    ? ((pluginChannelsQ.data ?? []).find((c) => c.name === channelName) ?? null)
    : null;
  // Only active plugins are listed: switched off, quarantined or a failed request.
  const pluginUnavailable = isPlugin && !pluginChannelsQ.isLoading && pluginChannel === null;
  const pluginCannotReply = pluginChannel?.can_reply === false;
  // An ended web chat is answered by e-mail to the address the visitor left
  // (the chat's own article address is a `.invalid` placeholder).
  const isWebchat = channelName === "WebChat";
  const queryClient = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();

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
  const refineReview = useRefineReview(setBody);
  const clearReview = refineReview.clear;
  const { review: reviewOpen, currentReviewText } = refineReview;
  const [reviewLength, setReviewLength] = useState(0);
  const [templateId, setTemplateId] = useState("");
  // Collected while writing, recorded after the article is created.
  const [mentions, setMentions] = useState<PickedMention[]>([]);
  const [timeUnits, setTimeUnits] = useState("");
  const attachments = useChatAttachments();
  /** What happens to the ticket once the reply is sent (see `NEXT_STATES`). */
  const [nextState, setNextState] = useState<NextState>("keep");
  /** Reminder date (local, `YYYY-MM-DD`) for "Wartend". */
  const [pendingDate, setPendingDate] = useState(defaultPendingDate);
  /** Set when the article went out but a mention/time write did not — the
   * dialog then offers a retry for those alone, never a second send. */
  const [extrasFailed, setExtrasFailed] = useState<Array<"mentions" | "time">>(
    [],
  );
  /** The article the send created — a retried booking links to it too. */
  const [sentArticleId, setSentArticleId] = useState<number | null>(null);
  // What the server-side reply draft seeded — the yardstick for "the agent
  // actually typed something". Stays null until the draft query resolves, so
  // nothing is persisted before there is anything to compare against.
  const baselineRef = useRef<{
    subject: string;
    body: string;
    to: string;
    cc: string;
  } | null>(null);
  /** Which target the fields were seeded for — see the seeding effect. */
  const seededKeyRef = useRef<string | null>(null);
  /** Bumped on every successful send. A queued debounce write compares the
   * value it was scheduled with and bails if a send happened meanwhile, so
   * the last keystroke before "Send" can't resurrect the draft. */
  const sendEpochRef = useRef(0);

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

  // Znuny RequiredLock: opening the composer locks the ticket and makes the
  // agent its owner; a foreign lock shows the takeover banner instead.
  const ticketLock = useComposerLock(ticketId, "compose", open);

  const storedDraft = useReplyDraft(ticketId, articleId);
  const draftsLoaded = useReplyDraftsLoaded(ticketId);
  const saveDraft = useSaveReplyDraft();
  const clearDraft = useClearReplyDraft();

  const chatQ = useTicketChat(ticketId, open && isWebchat);
  const chatLoading = isWebchat && chatQ.isPending;
  const visitorEmail = chatQ.data?.visitor_email?.trim() ?? "";

  const draftQ = useQuery({
    queryKey: [
      "tickets",
      ticketId,
      "articles",
      articleId,
      "reply-draft",
      replyAll,
    ],
    queryFn: () => api.getReplyDraft(ticketId, articleId, replyAll),
    enabled: open,
  });

  const templatesQ = useQuery({
    queryKey: ["tickets", ticketId, "templates"],
    queryFn: () => api.listTemplates(ticketId),
    enabled: open,
  });

  // Seed the fields once the draft arrives — from a stored unsent draft when
  // there is one, otherwise from the server's reply draft. The baseline is
  // always the server version, so restoring a stored draft keeps it correctly
  // marked as edited.
  useEffect(() => {
    const d = draftQ.data;
    if (!d) return;
    // Drafts live on the server now, so wait for them: seeding from the
    // server reply first and patching the stored draft in afterwards would
    // overwrite anything typed in between.
    if (!draftsLoaded) return;
    // The visitor's address decides the recipient — wait for it.
    if (chatLoading) return;
    // Whether this is a plugin channel decides the seed (no quote) — wait too.
    if (pluginChannelsQ.isLoading) return;
    // `initialDraft` is an object literal at the call site (AiPanel), so it
    // changes identity on every parent render — seed once per actual target
    // instead, or an unrelated re-render would wipe whatever is being typed.
    const seedKey = `${ticketId}:${articleId}:${replyAll}:${initialDraft?.id ?? ""}`;
    if (seededKeyRef.current === seedKey) return;
    seededKeyRef.current = seedKey;
    // Never `.invalid`: that is the placeholder of a visitor without an
    // address; the typed one (when there is one) replaces it.
    const mailable = (list: Recipient[]) =>
      isWebchat ? list.filter((r) => !isInvalidTldAddress(r.email)) : list;
    const serverTo = isPlugin
      ? []
      : isWebchat && visitorEmail.includes("@") && !isInvalidTldAddress(visitorEmail)
        ? [{ name: "", email: visitorEmail }]
        : mailable(parseRecipientList(d.to_address));
    const serverCc = mailable(parseRecipientList(d.cc));
    const serverSubject = initialDraft?.subject ?? d.subject;
    // Answer on top (blank, or the AI draft's text), blank line, then the
    // quoted original. A plugin channel is chat-like: no quoted original (it
    // would count against the channel's length limit).
    const serverBody = isPlugin
      ? (initialDraft?.body ?? "")
      : initialDraft
        ? `${initialDraft.body}\n\n${d.body}`
        : `\n\n${d.body}`;
    baselineRef.current = {
      subject: serverSubject,
      body: serverBody,
      to: joinRecipients(serverTo) ?? "",
      cc: joinRecipients(serverCc) ?? "",
    };

    const stored = getDraft(queryClient, ticketId, articleId);
    const nextTo = stored ? mailable(parseRecipientList(stored.to)) : serverTo;
    const nextCc = stored ? mailable(parseRecipientList(stored.cc)) : serverCc;
    const nextBcc = stored ? parseRecipientList(stored.bcc) : [];
    const nextReplyTo = stored?.replyTo ?? "";
    setTo(nextTo);
    setCc(nextCc);
    setBcc(nextBcc);
    setReplyTo(nextReplyTo);
    // Expand a section when it carries addresses; keep the rest collapsed.
    setShowCc(nextCc.length > 0);
    setShowBcc(nextBcc.length > 0);
    setShowReplyTo(nextReplyTo.trim().length > 0);
    setSubject(stored?.subject ?? serverSubject);
    clearReview();
    setBody(stored?.body ?? serverBody);
  }, [
    draftQ.data,
    draftsLoaded,
    chatLoading,
    pluginChannelsQ.isLoading,
    isPlugin,
    isWebchat,
    visitorEmail,
    initialDraft,
    queryClient,
    ticketId,
    articleId,
    replyAll,
    clearReview,
  ]);

  const aiDraftId = initialDraft?.id ?? null;

  // Mirror edits into the draft store, debounced so typing doesn't fire a
  // request per keystroke. Runs while closed too: the component stays mounted
  // after a backdrop click, and this is what makes that state survive a
  // reload — or a move to another device.
  useEffect(() => {
    const epoch = sendEpochRef.current;
    const timer = window.setTimeout(() => {
      // A send that happened while this write was queued invalidates it —
      // otherwise the last keystroke before "Send" would re-create the draft
      // right after the reply went out.
      if (sendEpochRef.current !== epoch) return;
      const baseline = baselineRef.current;
      if (!baseline) return;
      // While a refine review is open the text to keep is its current selection.
      const text = reviewOpen ? currentReviewText() : body;
      const toStr = joinRecipients(to) ?? "";
      const ccStr = joinRecipients(cc) ?? "";
      const dirty =
        text.trim() !== baseline.body.trim() ||
        subject !== baseline.subject ||
        toStr !== baseline.to ||
        ccStr !== baseline.cc ||
        bcc.length > 0 ||
        replyTo.trim().length > 0;
      if (dirty) {
        saveDraft({
          ticketId,
          articleId,
          replyAll,
          subject,
          body: text,
          to: toStr,
          cc: ccStr,
          bcc: joinRecipients(bcc) ?? "",
          replyTo,
          aiDraftId,
        });
      } else {
        // Edited back to the seeded state — no draft to advertise.
        clearDraft(ticketId, articleId);
      }
    }, 400);
    return () => window.clearTimeout(timer);
  }, [
    ticketId,
    articleId,
    replyAll,
    subject,
    body,
    reviewOpen,
    currentReviewText,
    to,
    cc,
    bcc,
    replyTo,
    aiDraftId,
    saveDraft,
    clearDraft,
  ]);

  /** Back to the server-seeded reply: blank answer above the quote, original
   * recipients, no template applied. Shared by "send" and "discard". */
  const resetToBaseline = () => {
    const baseline = baselineRef.current;
    if (!baseline) return;
    const nextCc = parseRecipientList(baseline.cc);
    setSubject(baseline.subject);
    setBody(baseline.body);
    refineReview.clear();
    setTo(parseRecipientList(baseline.to));
    setCc(nextCc);
    setBcc([]);
    setReplyTo("");
    setShowCc(nextCc.length > 0);
    setShowBcc(false);
    setShowReplyTo(false);
    setTemplateId("");
    attachments.removeMany(attachments.items.map((a) => a.id));
  };

  /** Everything recorded — reset the composer to the server-seeded reply and
   * close. The component stays mounted after closing, so without this the next
   * "Reply" on this article would open showing the message just sent. */
  const finishSend = () => {
    resetToBaseline();
    setMentions([]);
    setTimeUnits("");
    setExtrasFailed([]);
    nextStateTouched.current = false;
    setNextState("keep");
    onClose();
  };

  const templates = templatesQ.data ?? [];

  // "Danach" — see `useNextStateOptions` for who gets the control.
  const {
    canSetState,
    options: nextOptions,
    stateIdFor,
    payloadFor: nextStatePayload,
    defaultNext,
  } = useNextStateOptions(ticketId, open);
  const nextStateId = stateIdFor(nextState);
  // Follow the default ("Wartend" on a closed ticket) until the agent picks
  // something themselves; the ticket loads after the dialog opens.
  const nextStateTouched = useRef(false);
  useEffect(() => {
    if (open && !nextStateTouched.current) setNextState(defaultNext);
  }, [open, defaultNext]);

  // PGP / S/MIME for the outgoing mail (nothing shown when both are off).
  const security = useEmailSecurity({
    ticketId,
    to: joinRecipients(to),
    cc: joinRecipients(cc),
    bcc: joinRecipients(bcc),
    enabled: open && !isTelegram && !isPlugin,
  });

  const sendMutation = useMutation({
    mutationFn: async (text: string) => {
      const { article_id: sent } = await api.createArticle(ticketId, {
        sender_type: "agent",
        // Telegram has no subject line — the backend fills the ticket
        // title in. The field is hidden below, but seed it explicitly
        // rather than trusting `subject` never got edited some other way.
        subject: isTelegram ? "" : subject,
        body: text,
        content_type: "text/plain; charset=utf-8",
        // Unresolved plugin channel: Send is blocked; should it get here anyway, the
        // lower-cased name is the channel key and the backend refuses it (422).
        channel: isTelegram
          ? "telegram"
          : isPlugin
            ? (pluginChannel?.key ?? (channelName ?? "").toLowerCase())
            : "email",
        is_visible_for_customer: true,
        // Telegram has no addresses to send — the gateway routes by the
        // ticket's linked telegram_contact, not by header.
        // A plugin channel answers to the handle of the replied article
        // (the inbound sender), not to an e-mail address.
        to_address: isTelegram ? null : isPlugin ? pluginHandle || null : joinRecipients(to),
        cc: isTelegram || isPlugin ? null : joinRecipients(cc),
        bcc: isTelegram || isPlugin ? null : joinRecipients(bcc),
        reply_to: isTelegram || isPlugin ? null : replyTo.trim() || null,
        // Threading headers from the reply-draft endpoint so the outbound
        // Message-ID chain matches Znuny follow-up detection.
        in_reply_to: isTelegram || isPlugin ? null : (draftQ.data?.in_reply_to ?? null),
        references: isTelegram || isPlugin ? null : (draftQ.data?.references ?? null),
        ai_draft_id: aiDraftId,
        email_security: isTelegram || isPlugin ? null : security.payload,
        ...(attachments.payload.length > 0 ? { attachments: attachments.payload } : {}),
        ...nextStatePayload(nextState, pendingDate),
      });
      // The reply is out; mentions and the booking follow and may fail on
      // their own without costing the message.
      setSentArticleId(sent);
      return postComposerExtras(ticketId, {
        articleId: sent,
        body: text,
        mentions,
        timeUnits,
        queryClient,
      });
    },
    onSuccess: (extras) => {
      // Sent — the draft is no longer pending, drop it before anything can
      // re-render the placeholder.
      sendEpochRef.current += 1;
      clearDraft(ticketId, articleId);
      void queryClient.invalidateQueries({
        queryKey: ["tickets", ticketId, "articles"],
      });
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      if (nextStateId != null) {
        // A state change moves the ticket between list segments and counts.
        void queryClient.invalidateQueries({ queryKey: ["tickets"] });
        void queryClient.invalidateQueries({ queryKey: ["queues"] });
      }
      if (initialDraft) {
        // Sending with ai_draft_id accepts the draft server-side — it drops
        // out of the AI panel's open-drafts list.
        void queryClient.invalidateQueries({
          queryKey: ["tickets", ticketId, "ai"],
        });
      }
      if (extras.failed.length > 0) {
        // Keep the body — the retry re-reads the `@names` out of it.
        setExtrasFailed(extras.failed);
        return;
      }
      finishSend();
    },
  });

  /** Sends the body — or, while a refine review is open, exactly the text its
   * current on/off selection produces (as if "Übernehmen" had been pressed). */
  const send = () => {
    sendMutation.mutate(refineReview.flush() ?? body);
  };

  // Re-runs only the writes that failed, so a successful mention is never
  // duplicated by retrying a failed booking.
  const retryExtrasMutation = useMutation({
    mutationFn: () =>
      postComposerExtras(ticketId, {
        // Only reachable after a send succeeded, so the id is always set.
        articleId: sentArticleId!,
        body,
        mentions: extrasFailed.includes("mentions") ? mentions : [],
        timeUnits: extrasFailed.includes("time") ? timeUnits : "",
        queryClient,
      }),
    onSuccess: (extras) => {
      setExtrasFailed(extras.failed);
      if (extras.failed.length === 0) finishSend();
    },
  });

  // Discarding is deliberate and confirmed — closing the dialog keeps the
  // draft, so this is the only way to lose typed text.
  const onDiscardDraft = async () => {
    const ok = await confirm({
      title: t("ticket.draftDiscard"),
      message: t("ticket.draftDiscardConfirm"),
      confirmLabel: t("ticket.draftDiscardConfirmButton"),
      variant: "danger",
    });
    if (!ok) return;
    clearDraft(ticketId, articleId);
    resetToBaseline();
    setMentions([]);
    setTimeUnits("");
    onClose();
  };

  const onPickTemplate = (id: string) => {
    setTemplateId(id);
    const tpl = templates.find((x) => String(x.id) === id);
    if (tpl) {
      refineReview.clear();
      setBody((prev) => `${tpl.text}\n${prev}`);
    }
  };

  const title = useMemo(
    () =>
      pluginChannel
        ? t("ticket.replyPluginChannel", { label: pluginChannel.label })
        : replyAll
          ? t("ticket.replyAll")
          : t("ticket.replyDialogTitle"),
    [replyAll, t, pluginChannel],
  );
  const pluginHandle = draftQ.data?.to_address?.trim() ?? "";
  const maxLength = pluginChannel?.capabilities.max_length ?? null;
  // While a refine review is open, what goes out is its current selection, not `body`.
  const outgoingLength = reviewOpen ? reviewLength : body.length;
  const tooLong = maxLength !== null && outgoingLength > maxLength;
  const pluginNoHandle = pluginChannel !== null && !pluginCannotReply && pluginHandle.length === 0;

  // Nothing ever goes to the reserved `.invalid` TLD.
  const hasInvalidTld = [...to, ...cc, ...bcc].some((r) => isInvalidTldAddress(r.email));
  const noVisitorEmail = isWebchat && !chatLoading && to.length === 0;
  const canSend =
    body.trim().length > 0 &&
    !tooLong &&
    (isTelegram || (isPlugin ? pluginHandle.length > 0 : to.length > 0)) &&
    !pluginUnavailable &&
    !pluginCannotReply &&
    !hasInvalidTld &&
    !sendMutation.isPending &&
    !attachments.encoding &&
    ticketLock.lockedBy === null &&
    (isTelegram || isPlugin || !security.blocked);

  // Wide on large viewports (~80+ mono chars in the body); full-width on mobile.
  // Dialog base is max-w-md; this className overrides via cn().
  const dialogWidth =
    "w-full max-w-full sm:max-w-2xl md:max-w-3xl lg:max-w-4xl xl:max-w-5xl";

  // OFF: muted outline. ON: filled accent (expanded and/or has content).
  const toggleCls =
    "inline-flex items-center gap-1 rounded border border-hairline px-2 py-0.5 text-muted transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
  const toggleActiveCls =
    "border-accent/50 bg-accent-dim text-accent hover:text-accent";
  // Compact count pill (QueuesPage open-badge style) — only when collapsed + non-empty.
  const countBadgeCls =
    "rounded-full bg-accent-dim px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-accent";

  const replyToCount = replyTo.trim() ? 1 : 0;
  // "On" when the section is expanded OR still holds content while collapsed.
  const ccOn = showCc || cc.length > 0;
  const bccOn = showBcc || bcc.length > 0;
  const replyToOn = showReplyTo || replyToCount > 0;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      className={dialogWidth}
      footerClassName="flex-wrap bg-surface-subtle"
      footer={
        draftQ.isLoading || draftQ.isError ? undefined : (
          <>
            {canSetState && nextOptions.length > 1 && extrasFailed.length === 0 && (
              <span className="inline-flex flex-wrap items-center gap-2" data-testid="reply-next-state">
                <span className="text-xs text-muted">{t("ticket.replyNext.label")}</span>
                <span
                  role="group"
                  aria-label={t("ticket.replyNext.label")}
                  className="inline-flex gap-0.5 rounded-lg border border-hairline bg-surface p-0.5"
                >
                  {nextOptions.map((o) => (
                    <button
                      key={o.key}
                      type="button"
                      data-testid={`reply-next-${o.key}`}
                      aria-pressed={nextState === o.key}
                      onClick={() => {
                        nextStateTouched.current = true;
                        setNextState(o.key);
                      }}
                      style={{ "--seg": o.color } as React.CSSProperties}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                        nextState === o.key
                          ? "bg-[color-mix(in_srgb,var(--seg)_14%,transparent)] font-semibold text-[var(--seg)]"
                          : "text-muted hover:text-ink",
                      )}
                    >
                      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--seg)]" />
                      {t(`ticket.replyNext.${o.key}`)}
                    </button>
                  ))}
                </span>
                {needsDate(nextState) && (
                  <label className="inline-flex items-center gap-1.5 rounded-md bg-[color-mix(in_srgb,var(--color-state-pending)_12%,transparent)] px-2 py-1 text-xs text-[var(--color-state-pending)]">
                    {t("ticket.replyNext.until")}
                    <input
                      type="date"
                      value={pendingDate}
                      min={todayIso()}
                      onChange={(e) => setPendingDate(e.target.value)}
                      data-testid="reply-next-pending-date"
                      className="border-0 bg-transparent p-0 text-xs text-inherit focus:outline-none"
                    />
                  </label>
                )}
              </span>
            )}
            {storedDraft && (
              <Button
                variant="ghost"
                size="sm"
                data-testid="reply-discard-draft"
                className="hover:text-danger"
                onClick={() => void onDiscardDraft()}
              >
                {t("ticket.draftDiscard")}
              </Button>
            )}
            <span className="flex-1" />
            <Button variant="ghost" size="sm" onClick={onClose}>
              {t("ticket.composerCancel")}
            </Button>
            {extrasFailed.length > 0 ? (
              <Button
                variant="primary"
                size="sm"
                data-testid="reply-extras-retry"
                disabled={retryExtrasMutation.isPending}
                onClick={() => retryExtrasMutation.mutate()}
              >
                {t("ticket.extrasRetry")}
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                data-testid="reply-send"
                disabled={!canSend || (needsDate(nextState) && !pendingDate)}
                onClick={send}
              >
                {sendMutation.isPending
                  ? t("ticket.replySending")
                  : canSetState && nextState !== "keep"
                    ? t(`ticket.replyNext.send.${nextState}`)
                    : t("ticket.composerSend")}
              </Button>
            )}
          </>
        )
      }
    >
      {confirmDialog}
      {draftQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : draftQ.isError ? (
        <p className="text-sm text-danger">{t("ticket.replyDraftError")}</p>
      ) : (
        <div className="space-y-2" data-testid="reply-dialog">
          <ComposerLockBanner
            lockedBy={ticketLock.lockedBy}
            onTakeOver={ticketLock.takeOver}
            busy={ticketLock.takingOver}
          />
          {isTelegram ? (
            <p
              className="rounded border border-hairline bg-surface-subtle/60 px-2 py-1 text-xs text-muted"
              data-testid="reply-telegram-hint"
            >
              {t("ticket.replyViaTelegram")}
            </p>
          ) : isPlugin ? (
            <>
              <p
                className="rounded border border-hairline bg-surface-subtle/60 px-2 py-1 text-xs text-muted"
                data-testid="reply-plugin-channel"
              >
                {pluginChannel?.label ?? channelName}
                {pluginHandle && <span className="ml-1 font-mono">{pluginHandle}</span>}
              </p>
              {(pluginUnavailable || pluginCannotReply || pluginNoHandle) && (
                <p className="text-xs text-danger" role="alert" data-testid="reply-plugin-blocked">
                  {pluginUnavailable
                    ? t("ticket.replyPluginChannelUnavailable")
                    : pluginCannotReply
                      ? t("ticket.replyPluginChannelCannotReply", { label: pluginChannel?.label ?? "" })
                      : t("ticket.replyPluginChannelNoHandle")}
                </p>
              )}
            </>
          ) : (
            <>
              {hasInvalidTld && (
                <p className="text-xs text-danger" role="alert" data-testid="reply-invalid-tld">
                  {t("webchat.agent.invalidRecipient")}
                </p>
              )}
              {noVisitorEmail && (
                <p
                  className="rounded border border-hairline bg-surface-subtle/60 px-2 py-1 text-xs text-muted"
                  data-testid="reply-webchat-no-email"
                >
                  {t("webchat.agent.noVisitorEmail")}
                </p>
              )}
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
                  className={cn(toggleCls, ccOn && toggleActiveCls)}
                  data-testid="reply-toggle-cc"
                  data-active={ccOn ? "true" : "false"}
                  aria-expanded={showCc}
                  onClick={() => setShowCc((v) => !v)}
                >
                  {t("ticket.replyCc")}
                  {!showCc && cc.length > 0 && (
                    <span
                      className={countBadgeCls}
                      data-testid="reply-toggle-cc-count"
                    >
                      {cc.length}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  className={cn(toggleCls, bccOn && toggleActiveCls)}
                  data-testid="reply-toggle-bcc"
                  data-active={bccOn ? "true" : "false"}
                  aria-expanded={showBcc}
                  onClick={() => setShowBcc((v) => !v)}
                >
                  {t("ticket.replyBcc")}
                  {!showBcc && bcc.length > 0 && (
                    <span
                      className={countBadgeCls}
                      data-testid="reply-toggle-bcc-count"
                    >
                      {bcc.length}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  className={cn(toggleCls, replyToOn && toggleActiveCls)}
                  data-testid="reply-toggle-replyto"
                  data-active={replyToOn ? "true" : "false"}
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
            </>
          )}
          {!isTelegram && (!isPlugin || pluginChannel?.capabilities.subject === true) && (
            <label className="block text-xs text-muted">
              {t("ticket.replySubject")}
              <input
                className={inputCls}
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
              />
            </label>
          )}
          {!isTelegram && templates.length > 0 && (
            <label className="block text-xs text-muted">
              {t("ticket.replyTemplate")}
              <SelectField
                items={[
                  { value: "", label: t("ticket.replyTemplateNone") },
                  ...templates.map((tpl) => ({
                    value: String(tpl.id),
                    label: tpl.name,
                  })),
                ]}
                value={templateId}
                onChange={onPickTemplate}
                testId="reply-template-select"
              />
            </label>
          )}
          {/* One framed editor: the text, the signature that will be appended,
              the files, and a toolbar (refine, files, time) along its bottom edge. */}
          <ComposerEditorFrame
            testIdPrefix="reply"
            attachments={attachments}
            canAttach={!isPlugin || pluginChannel?.capabilities.attachments === true}
            timeUnits={timeUnits}
            onTimeUnitsChange={setTimeUnits}
            locked={extrasFailed.length > 0}
            // Telegram never appends a signature.
            signature={
              isTelegram || isPlugin || !draftQ.data
                ? null
                : { text: draftQ.data.signature, isHtml: draftQ.data.signature_is_html }
            }
            refine={
              // Rewrites only the agent's own text; the quoted original below
              // it is re-assembled from the untouched original (see replyQuote).
              // Not offered on Telegram — a chat reply has no tone/length to
              // refine against a quoted original.
              !isTelegram && (
                <RefineControls
                  target={{ ticket_id: ticketId }}
                  body={body}
                  onChange={refineReview.onEdit}
                  disabled={extrasFailed.length > 0}
                  testIdPrefix="reply-refine"
                  variant="toolbar"
                  onRefined={refineReview.onRefined}
                  appliedStats={refineReview.applied?.stats ?? null}
                  onShowChanges={refineReview.showChanges}
                  reviewOpen={refineReview.review !== null}
                />
              )
            }
          >
            {refineReview.review ? (
              <RefineDiffView
                key={refineReview.reviewKey}
                before={refineReview.review.before}
                after={refineReview.review.after}
                toneLabel={t(toneLabelKey(refineReview.review.tone))}
                onAccept={refineReview.accept}
                onDiscard={refineReview.discard}
                onGroupsChange={(text) => {
                  refineReview.onGroupsChange(text);
                  // the selection lives in a ref; mirror its length so the counter re-renders
                  setReviewLength(text.length);
                }}
                className="min-h-[19rem]"
              />
            ) : (
              <MentionTextarea
                value={body}
                onChange={refineReview.onEdit}
                mentions={mentions}
                onMentionsChange={setMentions}
                rows={12}
                bare
                className="font-mono text-[12.5px] leading-relaxed"
                testId="reply-body"
                ariaLabel={t("ticket.replyAnswer")}
                // The article is already sent at this point; editing on would
                // only resurrect a draft for a reply that no longer exists.
                readOnly={extrasFailed.length > 0}
              />
            )}
          </ComposerEditorFrame>
          {maxLength !== null && (
            <p
              className={cn("text-xs", tooLong ? "text-danger" : "text-muted")}
              role={tooLong ? "alert" : undefined}
              data-testid="reply-plugin-counter"
            >
              {outgoingLength} / {maxLength}
              {tooLong && (
                <span className="ml-2" data-testid="reply-plugin-too-long">
                  {t("ticket.replyPluginChannelTooLong", {
                    label: pluginChannel?.label ?? "",
                    max: maxLength,
                  })}
                </span>
              )}
            </p>
          )}
          {!isTelegram && !isPlugin && (
            <EmailSecurityControl security={security} testId="reply-security" />
          )}
          {sendMutation.isError && (
            <p className="text-xs text-danger" data-testid="reply-send-error">
              {sendMutation.error instanceof ApiError && sendMutation.error.status === 413
                ? t("ticket.telegram.composer.attachTooLargeServer")
                : sendMutation.error instanceof ApiError &&
                    sendMutation.error.message &&
                    !sendMutation.error.message.startsWith("HTTP ")
                  ? sendMutation.error.message
                  : t("ticket.replyError")}
            </p>
          )}
          {extrasFailed.length > 0 && (
            <p
              className="rounded border border-escalation/30 bg-escalation/15 px-2 py-1 text-xs text-escalation"
              data-testid="reply-extras-error"
            >
              {t(
                extrasFailed.length === 2
                  ? "ticket.extrasFailedBoth"
                  : extrasFailed[0] === "mentions"
                    ? "ticket.extrasFailedMentions"
                    : "ticket.extrasFailedTime",
              )}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}
