import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError, type TicketDetail } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { cn } from "@/lib/cn";
import { stateLabel } from "@/lib/status";
import { ticketPerms } from "@/lib/ticket";
import { phoneApi, type PhoneCallLockedDetail, type PhoneDirection } from "@/lib/phoneApi";
import {
  callbackState,
  clearPhoneDraft,
  defaultStateFor,
  elapsedToMinutes,
  formatElapsed,
  loadPhoneDraft,
  missingRequired,
  pendingIso,
  savePhoneDraft,
  timerFromCall,
  type PhoneNextState,
} from "@/lib/phoneCall";
import { ComposerBody } from "../ComposerBody";
import { ComposerTimeChip } from "../ComposerTimeChip";
import { RefineControls } from "../RefineControls";
import { toneLabelKey } from "@/lib/refineTone";
import { RefineDiffView } from "../RefineDiffView";
import { useRefineReview } from "../useRefineReview";
import { AttachmentChips } from "../telegram/ComposerChips";
import { useChatAttachments } from "../telegram/useChatAttachments";
import { DynamicFieldInputs, type DynamicFieldValues } from "./DynamicFieldInputs";
import { PendingTimeInput } from "./PendingTimeInput";
import { useCallTimer } from "./useCallTimer";

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

const segBase =
  "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

function customerName(ticket: TicketDetail): string {
  return ticket.customer_user_id || ticket.customer_id || "";
}

function autoSubject(t: (k: string, o?: Record<string, unknown>) => string, ticket: TicketDetail, direction: PhoneDirection) {
  const name = customerName(ticket);
  if (!name) return t(direction === "inbound" ? "phone.subjectInboundAnon" : "phone.subjectOutboundAnon");
  return t(direction === "inbound" ? "phone.subjectInbound" : "phone.subjectOutbound", { name });
}

/**
 * Log a phone call on a ticket (Znuny AgentTicketPhoneInbound/Outbound):
 * direction, a running call timer that prefills the booked time, subject,
 * notes (with "Notiz aufbereiten"), next state incl. a callback reminder,
 * customer visibility, ticket dynamic fields and attachments — one request to
 * `POST /tickets/{id}/phone-calls`.
 *
 * Mount it only while open: the timer starts on mount. Text, direction and
 * elapsed time are kept per ticket in localStorage until the call is saved
 * or the draft discarded, so closing the dialog mid-call loses nothing.
 */
export function PhoneCallDialog({
  ticket,
  initialDirection,
  callerNumber,
  startedAt,
  endedAt,
  onClose,
}: {
  ticket: TicketDetail;
  initialDirection: PhoneDirection;
  /** Number that was dialled (click-to-call) — logged with the call. */
  callerNumber?: string | null;
  /** CTI popup: epoch ms the call was answered — the timer counts from here
   * (wins over a draft's elapsed time). */
  startedAt?: number | null;
  /** CTI popup: epoch ms the call ended — the timer shows the fixed duration. */
  endedAt?: number | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const ticketId = ticket.id;
  const perms = ticketPerms(ticket);

  // Restored once on mount — the draft wins over the requested direction.
  const [draft] = useState(() => loadPhoneDraft(ticketId));
  const [direction, setDirection] = useState<PhoneDirection>(draft?.direction ?? initialDirection);
  const [subject, setSubject] = useState(() => draft?.subject || autoSubject(t, ticket, draft?.direction ?? initialDirection));
  const [subjectTouched, setSubjectTouched] = useState(Boolean(draft?.subject));
  const [body, setBody] = useState(draft?.body ?? "");
  const refineReview = useRefineReview(setBody);
  const { review: reviewOpen, currentReviewText } = refineReview;
  const [callTimer] = useState(() => timerFromCall(startedAt, endedAt));
  const timer = useCallTimer(callTimer ?? { initialSeconds: draft?.elapsed ?? 0 });
  const [timeUnits, setTimeUnits] = useState("");
  const [timeTouched, setTimeTouched] = useState(false);
  const [nextState, setNextState] = useState<PhoneNextState>(perms.rw ? "default" : "keep");
  const [callbackAt, setCallbackAt] = useState("");
  const [visible, setVisible] = useState(true);
  const [dfValues, setDfValues] = useState<DynamicFieldValues>({});
  const attachments = useChatAttachments();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const statesQ = useQuery({
    queryKey: ["reference", "states"],
    queryFn: () => api.listReferenceStates(),
  });
  const screen = direction === "inbound" ? "AgentTicketPhoneInbound" : "AgentTicketPhoneOutbound";
  const fieldsQ = useQuery({
    queryKey: ["reference", "dynamic-fields", screen],
    queryFn: () => phoneApi.screenDynamicFields(screen),
  });
  const states = useMemo(() => statesQ.data ?? [], [statesQ.data]);
  const defaultState = defaultStateFor(direction, states);
  const reminderState = callbackState(states);
  const fields = useMemo(() => fieldsQ.data ?? [], [fieldsQ.data]);

  // Current ticket values seed the dynamic-field editors.
  useEffect(() => {
    if (fields.length === 0) return;
    setDfValues((prev) => {
      const next = { ...prev };
      for (const f of fields) {
        if (f.name in next) continue;
        const current = ticket.dynamic_fields?.find((df) => df.name === f.name);
        next[f.name] = (current?.values ?? []).map(String);
      }
      return next;
    });
  }, [fields, ticket.dynamic_fields]);

  // Minutes the chip shows while the agent has not typed a value of their
  // own. The chip keeps its own text, so it is re-keyed to pick up a new
  // minute — only while untouched, or the first keystroke would remount it.
  const autoMinutes = elapsedToMinutes(timer.elapsed);
  const [chipKey, setChipKey] = useState(0);
  useEffect(() => {
    if (!timeTouched) setChipKey(autoMinutes);
  }, [autoMinutes, timeTouched]);
  const effectiveTime = timeTouched ? timeUnits : autoMinutes > 0 ? String(autoMinutes) : "";

  const pickDirection = (next: PhoneDirection) => {
    setDirection(next);
    if (!subjectTouched) setSubject(autoSubject(t, ticket, next));
  };

  // Draft: persisted while there is something typed; debounced.
  const savedRef = useRef(false);
  useEffect(() => {
    const handle = window.setTimeout(() => {
      if (savedRef.current) return;
      // While a refine review is open the text to keep is its current selection.
      const text = reviewOpen ? currentReviewText() : body;
      if (text.trim() || subjectTouched) {
        savePhoneDraft(ticketId, {
          direction,
          subject: subjectTouched ? subject : "",
          body: text,
          elapsed: timer.elapsed,
        });
      }
    }, 400);
    return () => window.clearTimeout(handle);
  }, [ticketId, direction, subject, subjectTouched, body, timer.elapsed, reviewOpen, currentReviewText]);

  const missing = missingRequired(fields, dfValues);
  const callbackIso = pendingIso(callbackAt);

  const stateFields = (): { state_id?: number; pending_time?: string } => {
    if (!perms.rw) return {};
    if (nextState === "default" && defaultState) return { state_id: defaultState.id };
    if (nextState === "callback" && reminderState && callbackIso) {
      return { state_id: reminderState.id, pending_time: callbackIso };
    }
    return {};
  };

  const changedFields = (): Record<string, string[]> => {
    const out: Record<string, string[]> = {};
    for (const f of fields) {
      const now = dfValues[f.name] ?? [];
      const before = (ticket.dynamic_fields?.find((df) => df.name === f.name)?.values ?? []).map(String);
      if (JSON.stringify(now) !== JSON.stringify(before)) out[f.name] = now;
    }
    return out;
  };

  const saveMutation = useMutation({
    mutationFn: (text: string) => {
      const units = Number(effectiveTime);
      return phoneApi.logPhoneCall(ticketId, {
        direction,
        subject: subject.trim(),
        body: text,
        content_type: "text/plain",
        is_visible_for_customer: visible,
        time_unit: Number.isFinite(units) && units > 0 ? units : null,
        dynamic_fields: changedFields(),
        attachments: attachments.payload,
        caller_number: callerNumber ?? null,
        ...stateFields(),
      });
    },
    onSuccess: () => {
      savedRef.current = true;
      clearPhoneDraft(ticketId);
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId] });
      if (nextState !== "keep") {
        void queryClient.invalidateQueries({ queryKey: ["tickets"] });
        void queryClient.invalidateQueries({ queryKey: ["queues"] });
      }
      onClose();
    },
  });

  const lockedBy =
    saveMutation.error instanceof ApiError && saveMutation.error.status === 409
      ? ((saveMutation.error.detail as { detail?: PhoneCallLockedDetail } | null)?.detail
          ?.locked_by_name ?? "?")
      : null;

  // Also the way out of a call that never connected (hung up at the desk
  // phone): stops the timer and drops the draft. Asks only when there is
  // text or an attachment to lose.
  const onDiscard = async () => {
    const hasContent =
      body.trim() !== "" || subjectTouched || attachments.items.length > 0 || draft !== null;
    if (hasContent) {
      const ok = await confirm({
        title: t("phone.discard"),
        message: t("phone.discardConfirm"),
        confirmLabel: t("ticket.draftDiscardConfirmButton"),
        variant: "danger",
      });
      if (!ok) return;
    }
    savedRef.current = true;
    clearPhoneDraft(ticketId);
    onClose();
  };

  const canSave =
    subject.trim().length > 0 &&
    !saveMutation.isPending &&
    !attachments.encoding &&
    missing.length === 0 &&
    !(nextState === "callback" && !callbackIso);

  const nextOptions: { key: PhoneNextState; label: string; color: string }[] = [
    { key: "keep", label: t("phone.next.keep"), color: "var(--color-muted)" },
    ...(defaultState
      ? [
          {
            key: "default" as const,
            label: stateLabel(t, defaultState.name),
            color: defaultState.type_name.startsWith("closed")
              ? "var(--color-state-closed)"
              : "var(--color-state-open)",
          },
        ]
      : []),
    ...(reminderState
      ? [{ key: "callback" as const, label: t("phone.next.callback"), color: "var(--color-state-pending)" }]
      : []),
  ];

  return (
    <Dialog
      open
      onClose={onClose}
      title={t(direction === "inbound" ? "phone.dialogTitleInbound" : "phone.dialogTitleOutbound")}
      className="w-full max-w-full sm:max-w-2xl md:max-w-3xl"
      footerClassName="flex-wrap bg-surface-subtle"
      footer={
        <>
          <Button
            variant="ghost"
            size="sm"
            data-testid="phone-discard"
            className="hover:text-danger"
            onClick={() => void onDiscard()}
          >
            {t("phone.discard")}
          </Button>
          <span className="flex-1" />
          <Button variant="ghost" size="sm" onClick={onClose} data-testid="phone-cancel">
            {t("ticket.composerCancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            data-testid="phone-save"
            disabled={!canSave}
            onClick={() => saveMutation.mutate(refineReview.flush() ?? body)}
          >
            {saveMutation.isPending ? t("phone.saving") : t("phone.save")}
          </Button>
        </>
      }
    >
      {confirmDialog}
      <div className="space-y-3" data-testid="phone-dialog">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span
            role="group"
            aria-label={t("phone.direction")}
            className="inline-flex gap-0.5 rounded-lg border border-hairline bg-surface p-0.5"
          >
            {(["inbound", "outbound"] as const).map((d) => (
              <button
                key={d}
                type="button"
                data-testid={`phone-direction-${d}`}
                aria-pressed={direction === d}
                onClick={() => pickDirection(d)}
                className={cn(segBase, direction === d ? "bg-accent-dim font-semibold text-accent" : "text-muted hover:text-ink")}
              >
                {d === "inbound" ? "↙" : "↗"} {t(`phone.${d}`)}
              </button>
            ))}
          </span>
          <span
            className="inline-flex items-center gap-2 rounded-md border border-hairline bg-surface px-2 py-1 text-xs"
            title={t("phone.timer")}
          >
            <span
              aria-hidden
              className={cn("h-2 w-2 rounded-full", timer.running ? "animate-pulse bg-danger" : "bg-muted")}
            />
            <span className="font-mono tabular-nums text-ink" data-testid="phone-timer">
              {formatElapsed(timer.elapsed)}
            </span>
            <button
              type="button"
              data-testid="phone-timer-toggle"
              onClick={timer.running ? timer.pause : timer.resume}
              className="rounded px-1 text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            >
              {timer.running ? t("phone.pause") : t("phone.resume")}
            </button>
          </span>
        </div>

        <label className="block text-xs text-muted">
          {t("phone.subject")}
          <input
            className={inputCls}
            value={subject}
            data-testid="phone-subject"
            onChange={(e) => {
              setSubject(e.target.value);
              setSubjectTouched(true);
            }}
          />
        </label>

        <div className="overflow-hidden rounded-lg border border-hairline bg-surface-subtle/40 focus-within:border-accent/60">
          <div className="p-2">
            <span className="mb-1 block text-xs text-muted">{t("phone.notes")}</span>
            {refineReview.review ? (
              <RefineDiffView
                key={refineReview.reviewKey}
                before={refineReview.review.before}
                after={refineReview.review.after}
                toneLabel={t(toneLabelKey(refineReview.review.tone))}
                onAccept={refineReview.accept}
                onDiscard={refineReview.discard}
                onGroupsChange={refineReview.onGroupsChange}
                className="min-h-[12rem] rounded-md border border-hairline"
              />
            ) : (
              <ComposerBody
                richText={false}
                value={body}
                onChange={refineReview.onEdit}
                testId="phone-body"
              />
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-hairline bg-surface px-2 py-1.5">
            <RefineControls
              target={{ ticket_id: ticketId }}
              body={body}
              onChange={refineReview.onEdit}
              onRefined={refineReview.onRefined}
              appliedStats={refineReview.applied?.stats ?? null}
              onShowChanges={refineReview.showChanges}
              reviewOpen={refineReview.review !== null}
              testIdPrefix="phone-refine"
              variant="toolbar"
              mode="call_note"
            />
            <span className="ml-auto">
              <ComposerTimeChip
                key={chipKey}
                value={effectiveTime}
                onChange={(v) => {
                  setTimeTouched(true);
                  setTimeUnits(v);
                }}
                testId="phone-time"
              />
            </span>
          </div>
        </div>

        {perms.rw && nextOptions.length > 1 && (
          <div className="flex flex-wrap items-center gap-2" data-testid="phone-next-state">
            <span className="text-xs text-muted">{t("phone.next.label")}</span>
            <span
              role="group"
              aria-label={t("phone.next.label")}
              className="inline-flex gap-0.5 rounded-lg border border-hairline bg-surface p-0.5"
            >
              {nextOptions.map((o) => (
                <button
                  key={o.key}
                  type="button"
                  data-testid={`phone-next-${o.key}`}
                  aria-pressed={nextState === o.key}
                  onClick={() => setNextState(o.key)}
                  style={{ "--seg": o.color } as React.CSSProperties}
                  className={cn(
                    segBase,
                    nextState === o.key
                      ? "bg-[color-mix(in_srgb,var(--seg)_14%,transparent)] font-semibold text-[var(--seg)]"
                      : "text-muted hover:text-ink",
                  )}
                >
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--seg)]" />
                  {o.label}
                </button>
              ))}
            </span>
            {nextState === "callback" && (
              <PendingTimeInput value={callbackAt} onChange={setCallbackAt} testId="phone-callback" />
            )}
          </div>
        )}

        {fields.length > 0 && (
          <details className="rounded border border-hairline px-2 py-1.5" open={missing.length > 0}>
            <summary className="cursor-pointer text-xs text-muted">{t("phone.fields")}</summary>
            <div className="mt-2">
              <DynamicFieldInputs
                fields={fields}
                values={dfValues}
                onChange={(name, values) => setDfValues((prev) => ({ ...prev, [name]: values }))}
                testId="phone-df"
              />
            </div>
          </details>
        )}

        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="inline-flex items-center gap-1.5 text-ink">
            <input
              type="checkbox"
              data-testid="phone-visible"
              checked={visible}
              onChange={(e) => setVisible(e.target.checked)}
            />
            {t("phone.visible")}
          </label>
          <button
            type="button"
            data-testid="phone-attach"
            onClick={() => fileInputRef.current?.click()}
            className="rounded border border-hairline px-2 py-0.5 text-muted hover:text-ink"
          >
            📎 {t("phone.attach")}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            data-testid="phone-attach-input"
            onChange={(e) => {
              if (e.target.files) attachments.add(e.target.files);
              e.target.value = "";
            }}
          />
          <AttachmentChips items={attachments.items} onRemove={attachments.remove} />
        </div>

        {missing.length > 0 && (
          <p className="text-xs text-danger" data-testid="phone-df-missing">
            {t("phone.dfRequired", {
              fields: fields.filter((f) => missing.includes(f.name)).map((f) => f.label).join(", "),
            })}
          </p>
        )}
        {lockedBy !== null ? (
          <p className="rounded border border-escalation/30 bg-escalation/15 px-2 py-1 text-xs text-escalation" data-testid="phone-locked">
            {t("phone.lockedBy", { name: lockedBy })}
          </p>
        ) : (
          saveMutation.isError && (
            <p className="text-xs text-danger" data-testid="phone-error">
              {saveMutation.error instanceof ApiError &&
              saveMutation.error.message &&
              !saveMutation.error.message.startsWith("HTTP ")
                ? saveMutation.error.message
                : t("phone.error")}
            </p>
          )
        )}
      </div>
    </Dialog>
  );
}
