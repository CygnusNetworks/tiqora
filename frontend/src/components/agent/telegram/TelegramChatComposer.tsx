import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError, type ArticleCreateRequest, type TelegramButtonIn, type TemplateOut } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { cn } from "@/lib/cn";
import { useComposerLock } from "@/lib/composerLock";
import { telegramChatKey, useTelegramChat } from "@/lib/telegramChatApi";
import { ticketAiApi } from "@/lib/ticketAiApi";
import { ComposerLockBanner } from "../ComposerLock";
import { defaultPendingDate, useNextStateOptions, type NextState } from "../replyNextState";
import { ButtonEditor } from "./ButtonEditor";
import { ChatSnippetPicker } from "./ChatSnippetPicker";
import {
  cleanButtons,
  filterSnippets,
  findSlashQuery,
  MAX_MESSAGE_LENGTH,
  RESOLVED_PRESET_BODY,
  RESOLVED_PRESET_BUTTONS,
  snippetText,
} from "./chatComposerHelpers";
import { AttachmentChips, QuoteChip } from "./ComposerChips";
import { ComposerFooter } from "./ComposerFooter";
import { useComposerRequests } from "./composerBus";
import { MAX_ATTACHMENT_BYTES, formatBytes, useChatAttachments } from "./useChatAttachments";
import { useChatDraftSync } from "./useChatDraftSync";
import { useTypingPing } from "./useTypingPing";

/**
 * Messenger-style input bar under a Telegram conversation — replaces the
 * reply dialog there. Enter sends; the reply goes out with an empty subject
 * (the backend fills in the ticket title) and no signature. Quote, buttons,
 * attachments and the state afterwards ride along in the same
 * create-article call, so a failed send leaves everything in place for
 * "Erneut senden".
 */
export function TelegramChatComposer({
  ticketId,
  onComposingChange,
}: {
  ticketId: number;
  /** Presence heartbeat: true while there is something unsent. */
  onComposingChange?: (composing: boolean) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const [body, setBody] = useState("");
  const [caret, setCaret] = useState(0);
  const [quoteId, setQuoteId] = useState<number | null>(null);
  const [aiDraftId, setAiDraftId] = useState<number | null>(null);
  const [buttonsOn, setButtonsOn] = useState(false);
  const [buttons, setButtons] = useState<TelegramButtonIn[]>([]);
  const [nextState, setNextState] = useState<NextState>("keep");
  const [pendingDate, setPendingDate] = useState(defaultPendingDate);
  /** Slash position the agent dismissed with Esc — typing on keeps it shut. */
  const [snippetDismissed, setSnippetDismissed] = useState<number | null>(null);
  const [snippetIndex, setSnippetIndex] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  /** Caret to restore after a programmatic edit (snippet insert). */
  const caretAfterRef = useRef<number | null>(null);

  const attachments = useChatAttachments();
  const ping = useTypingPing(ticketId);
  const hasText = body.trim().length > 0;
  const hasContent = hasText || attachments.items.length > 0;

  const ticketLock = useComposerLock(ticketId, "compose", hasContent);
  const next = useNextStateOptions(ticketId, true);
  const chat = useTelegramChat(ticketId, true).data ?? null;
  const aiQ = useQuery({
    queryKey: ["tickets", ticketId, "ai"],
    queryFn: ({ signal }) => ticketAiApi.getState(ticketId, signal),
  });
  const suggestion = aiQ.data?.drafts.find((d) => d.status === "open" && d.id !== aiDraftId) ?? null;
  const templatesQ = useQuery({
    queryKey: ["tickets", ticketId, "templates", "Chat"],
    queryFn: () => api.listTemplates(ticketId, "Chat"),
  });

  // Presence: report changes, and "done" when the composer goes away (view
  // switch) so a stale "composing" doesn't outlive it.
  const composingCb = useRef(onComposingChange);
  composingCb.current = onComposingChange;
  useEffect(() => {
    composingCb.current?.(hasContent);
  }, [hasContent]);
  useEffect(() => () => composingCb.current?.(false), []);

  // A draft handed over from elsewhere (AiPanel's "Entwurf übernehmen") —
  // same overwrite guard as the composer's own inline AI suggestion below.
  const applyExternalDraft = async (draft: { id: number; body: string }) => {
    if (body.trim() && body !== draft.body) {
      const ok = await confirm({
        title: t("ticket.telegram.composer.aiReplaceTitle"),
        message: t("ticket.telegram.composer.aiReplaceMessage"),
        confirmLabel: t("ticket.telegram.composer.aiReplaceConfirm"),
        variant: "danger",
      });
      if (!ok) return;
    }
    setBody(draft.body);
    setCaret(draft.body.length);
    setAiDraftId(draft.id);
    inputRef.current?.focus();
  };

  useComposerRequests(ticketId, (req) => {
    if (req.quoteArticleId != null) setQuoteId(req.quoteArticleId);
    if (req.draft) void applyExternalDraft(req.draft);
    if (req.focus) inputRef.current?.focus();
  });

  // Whatever was typed before the stored draft arrived wins over it.
  const draftSync = useChatDraftSync(ticketId, { body, quoteArticleId: quoteId, aiDraftId }, (d) => {
    setBody((prev) => prev || d.body);
    setQuoteId((prev) => prev ?? d.quoteArticleId);
    setAiDraftId((prev) => prev ?? d.aiDraftId);
  });

  // ── Textarea: grow with the text (CSS caps it at ~6 lines) ──
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
    if (caretAfterRef.current !== null) {
      el.setSelectionRange(caretAfterRef.current, caretAfterRef.current);
      caretAfterRef.current = null;
    }
  }, [body]);

  // ── Snippets ──
  const slash = findSlashQuery(body, caret);
  const templates = templatesQ.data ?? [];
  const matches = slash && slash.start !== snippetDismissed ? filterSnippets(templates, slash.query) : [];
  const pickerOpen = matches.length > 0;
  const activeSnippet = Math.min(snippetIndex, matches.length - 1);

  const pickSnippet = (tpl: TemplateOut) => {
    if (!slash) return;
    const text = snippetText(tpl);
    const pos = slash.start + text.length;
    setBody(body.slice(0, slash.start) + text + body.slice(slash.end));
    setCaret(pos);
    caretAfterRef.current = pos;
    setSnippetIndex(0);
    inputRef.current?.focus();
  };

  // ── Send ──
  const tooLong = body.length > MAX_MESSAGE_LENGTH;
  const canSend =
    hasContent &&
    !tooLong &&
    !attachments.encoding &&
    ticketLock.lockedBy === null &&
    !(nextState === "pending" && !pendingDate);

  // What the in-flight send took, so success clears exactly that and keeps
  // anything the agent typed, attached or changed while it was on its way.
  type Sent = {
    payload: ArticleCreateRequest;
    attachmentIds: number[];
    quoteId: number | null;
    aiDraftId: number | null;
    buttons: TelegramButtonIn[];
    nextState: NextState;
  };
  const latest = useRef({ buttons, nextState });
  latest.current = { buttons, nextState };

  const send = useMutation({
    mutationFn: (sent: Sent) => api.createArticle(ticketId, sent.payload),
    onSuccess: (_res, sent) => {
      const { payload } = sent;
      draftSync.markSent();
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "articles"] });
      void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
      void qc.invalidateQueries({ queryKey: telegramChatKey(ticketId) });
      if (payload.state_id != null) {
        // A state change moves the ticket between list segments and counts.
        void qc.invalidateQueries({ queryKey: ["tickets"] });
        void qc.invalidateQueries({ queryKey: ["queues"] });
      }
      if (payload.ai_draft_id != null) {
        // Sending with ai_draft_id accepts the draft server-side.
        void qc.invalidateQueries({ queryKey: ["tickets", ticketId, "ai"] });
      }
      setBody((b) => (b === payload.body ? "" : b));
      attachments.removeMany(sent.attachmentIds);
      setQuoteId((q) => (q === sent.quoteId ? null : q));
      setAiDraftId((a) => (a === sent.aiDraftId ? null : a));
      if (latest.current.buttons === sent.buttons) {
        setButtons([]);
        setButtonsOn(false);
      }
      if (latest.current.nextState === sent.nextState) {
        setNextState("keep");
        setPendingDate(defaultPendingDate());
      }
      inputRef.current?.focus();
    },
  });

  const submit = () => {
    if (!canSend || send.isPending) return;
    const telegramButtons = buttonsOn ? cleanButtons(buttons) : [];
    send.mutate({
      payload: {
        sender_type: "agent",
        // Empty on purpose: the backend uses the ticket title for Telegram.
        subject: "",
        body,
        content_type: "text/plain; charset=utf-8",
        channel: "telegram",
        is_visible_for_customer: true,
        ...(attachments.payload.length > 0 ? { attachments: attachments.payload } : {}),
        telegram_reply_to_article_id: quoteId,
        ...(telegramButtons.length > 0 ? { telegram_buttons: telegramButtons } : {}),
        ai_draft_id: aiDraftId,
        ...next.payloadFor(nextState, pendingDate),
      },
      attachmentIds: attachments.payloadIds,
      quoteId,
      aiDraftId,
      buttons,
      nextState,
    });
  };

  const sendError = !send.error
    ? null
    : send.error instanceof ApiError && send.error.status === 413
      ? // nginx refuses the body before the API sees it — its HTML page is
        // no message for the agent.
        t("ticket.telegram.composer.attachTooLargeServer")
      : send.error instanceof ApiError && !send.error.message.startsWith("HTTP ")
        ? send.error.message
        : t("ticket.telegram.composer.sendError");

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // IME composition (e.g. Japanese input) uses Enter to confirm a word.
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (pickerOpen && slash) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const step = e.key === "ArrowDown" ? 1 : -1;
        setSnippetIndex((activeSnippet + step + matches.length) % matches.length);
        return;
      }
      if ((e.key === "Enter" && !e.shiftKey) || e.key === "Tab") {
        e.preventDefault();
        pickSnippet(matches[activeSnippet]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSnippetDismissed(slash.start);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const onChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    const pos = e.target.selectionStart ?? value.length;
    setBody(value);
    setCaret(pos);
    setSnippetIndex(0);
    if (!findSlashQuery(value, pos)) setSnippetDismissed(null);
    if (value.trim()) ping();
  };

  const takeSuggestion = async () => {
    if (!suggestion) return;
    // Taking the suggestion replaces the field — don't lose typed text silently.
    if (body.trim() && body !== suggestion.body) {
      const ok = await confirm({
        title: t("ticket.telegram.composer.aiReplaceTitle"),
        message: t("ticket.telegram.composer.aiReplaceMessage"),
        confirmLabel: t("ticket.telegram.composer.aiReplaceConfirm"),
        variant: "danger",
      });
      if (!ok) return;
    }
    setBody(suggestion.body);
    setCaret(suggestion.body.length);
    setAiDraftId(suggestion.id);
    inputRef.current?.focus();
  };

  const applyPreset = () => {
    setButtons(RESOLVED_PRESET_BUTTONS.map((b) => ({ ...b })));
    if (!body.trim()) setBody(RESOLVED_PRESET_BODY);
  };

  const name = chat?.display_name || chat?.username || t("ticket.telegram.composer.customer");

  return (
    <div
      data-testid="tg-composer"
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        // Moving over a child fires dragleave on the parent too.
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        setDragOver(false);
      }}
      onDrop={(e) => {
        setDragOver(false);
        if (e.dataTransfer.files.length === 0) return;
        e.preventDefault();
        attachments.add(e.dataTransfer.files);
      }}
      className={cn(
        "grid gap-2 rounded-lg border bg-surface p-2.5",
        dragOver ? "border-accent ring-1 ring-accent" : "border-hairline",
      )}
    >
      {confirmDialog}
      <ComposerLockBanner
        lockedBy={ticketLock.lockedBy}
        onTakeOver={ticketLock.takeOver}
        busy={ticketLock.takingOver}
      />

      {suggestion && (
        <div
          data-testid="tg-composer-ai"
          className="flex min-w-0 items-center gap-2 rounded-lg border border-purple/30 bg-purple/10 px-2.5 py-1.5 text-xs"
        >
          <span className="shrink-0 font-semibold text-purple">✦ {t("ticket.telegram.composer.aiSuggestion")}</span>
          <span className="min-w-0 flex-1 truncate text-ink">{suggestion.body}</span>
          <button
            type="button"
            data-testid="tg-composer-ai-take"
            onClick={() => void takeSuggestion()}
            className="shrink-0 font-semibold text-purple hover:underline"
          >
            {t("ticket.telegram.composer.aiTake")}
          </button>
        </div>
      )}

      {(quoteId !== null || attachments.items.length > 0) && (
        <div className="flex min-w-0 flex-wrap gap-1.5">
          {quoteId !== null && (
            <QuoteChip ticketId={ticketId} articleId={quoteId} onRemove={() => setQuoteId(null)} />
          )}
          <AttachmentChips items={attachments.items} onRemove={attachments.remove} />
        </div>
      )}

      {buttonsOn && <ButtonEditor buttons={buttons} onChange={setButtons} onPreset={applyPreset} />}

      <div className="relative flex items-end gap-2">
        {pickerOpen && (
          <ChatSnippetPicker matches={matches} activeIndex={activeSnippet} onPick={pickSnippet} />
        )}
        <button
          type="button"
          title={t("ticket.telegram.composer.attach")}
          aria-label={t("ticket.telegram.composer.attach")}
          onClick={() => fileRef.current?.click()}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-hairline text-muted hover:text-ink"
        >
          ⊕
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          data-testid="tg-composer-file"
          onChange={(e) => {
            if (e.target.files) attachments.add(e.target.files);
            // Same file again must still fire a change.
            e.target.value = "";
          }}
        />
        <textarea
          ref={inputRef}
          rows={1}
          value={body}
          data-testid="tg-composer-input"
          aria-label={t("ticket.telegram.composer.inputLabel")}
          placeholder={t("ticket.telegram.composer.placeholder", { name })}
          onChange={onChange}
          onKeyDown={onKeyDown}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
          onPaste={(e) => {
            const files = e.clipboardData?.files;
            if (!files || files.length === 0) return;
            e.preventDefault();
            attachments.add(files);
          }}
          className="max-h-[8.25rem] min-h-[2rem] flex-1 resize-none overflow-y-auto rounded-lg border border-hairline bg-bg px-3 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
        />
        <Button
          variant="primary"
          size="sm"
          data-testid="tg-composer-send"
          disabled={!canSend || send.isPending}
          onClick={submit}
          className="h-8 shrink-0"
        >
          {send.isPending ? t("ticket.telegram.composer.sending") : t("ticket.telegram.composer.send")}
        </Button>
      </div>

      {attachments.error && (
        <p className="text-xs text-danger" data-testid="tg-composer-attach-error">
          {attachments.error === "tooLarge"
            ? t("ticket.telegram.composer.attachTooLarge", { max: formatBytes(MAX_ATTACHMENT_BYTES) })
            : t("ticket.telegram.composer.attachReadFailed")}
        </p>
      )}
      {sendError && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-danger" data-testid="tg-composer-error">
          <span>{sendError}</span>
          <button
            type="button"
            data-testid="tg-composer-retry"
            disabled={!canSend || send.isPending}
            onClick={submit}
            className="font-semibold underline disabled:opacity-50"
          >
            {t("ticket.telegram.composer.retry")}
          </button>
        </p>
      )}

      <ComposerFooter
        length={body.length}
        buttonsOn={buttonsOn}
        onToggleButtons={() => setButtonsOn((v) => !v)}
        nextOptions={next.canSetState ? next.options : []}
        nextState={nextState}
        onNextState={setNextState}
        pendingDate={pendingDate}
        onPendingDate={setPendingDate}
      />
    </div>
  );
}
