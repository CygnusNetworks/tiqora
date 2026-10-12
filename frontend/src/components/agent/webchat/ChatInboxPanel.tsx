import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import {
  chatAgentStateKey,
  chatErrorCode,
  chatInboxKey,
  webchatApi,
  type ChatInboxItem,
} from "@/lib/webchatApi";
import { ChatAvailabilityForm } from "./ChatAvailabilityForm";

function useAgeSeconds(since: string | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!since) return;
    const id = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(id);
  }, [since]);
  if (!since) return null;
  const ts = new Date(since).getTime();
  return Number.isNaN(ts) ? null : Math.max(0, Math.floor((now - ts) / 1000));
}

function WaitingRow({
  item,
  onAccept,
  busy,
  takeover = false,
}: {
  item: ChatInboxItem;
  onAccept: (item: ChatInboxItem) => void;
  busy: boolean;
  /** A chat the assistant is still handling: "Übernehmen" instead of "Annehmen". */
  takeover?: boolean;
}) {
  const { t } = useTranslation();
  const age = useAgeSeconds(item.waiting_since);
  const ageLabel =
    age === null
      ? ""
      : age < 60
        ? t("webchat.agent.ageSeconds", { count: age })
        : age < 3600
          ? t("webchat.agent.ageMinutes", { count: Math.floor(age / 60) })
          : t("webchat.agent.ageHours", { count: Math.floor(age / 3600) });
  // Visitor-supplied strings: rendered as text nodes only.
  const name = item.visitor_name?.trim() || t("webchat.agent.visitorFallback");
  return (
    <li
      className="flex items-center justify-between gap-2 px-3 py-2"
      data-testid={`chat-waiting-${item.session_id}`}
    >
      <div className="min-w-0">
        <div className="truncate text-[13px] font-medium text-ink">{name}</div>
        <div className="truncate text-[11.5px] text-muted">
          {[
            item.topic_label,
            takeover
              ? t("webchat.agent.bot.status")
              : ageLabel
                ? t("webchat.agent.statusWaiting", { age: ageLabel })
                : t("webchat.agent.statusWaitingNoAge"),
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
      </div>
      <Button
        size="sm"
        disabled={busy}
        data-testid={`chat-accept-${item.session_id}`}
        aria-label={`${takeover ? t("webchat.agent.takeover") : t("webchat.agent.accept")}: ${name}`}
        onClick={() => onAccept(item)}
      >
        {takeover ? t("webchat.agent.takeover") : t("webchat.agent.accept")}
      </Button>
    </li>
  );
}

/**
 * Popover content of the header chat button: availability, waiting chats
 * with an accept button and the agent's own running chats.
 */
export function ChatInboxPanel({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  const inboxQ = useQuery({
    queryKey: chatInboxKey,
    queryFn: ({ signal }) => webchatApi.inbox(signal),
  });
  const stateQ = useQuery({
    queryKey: chatAgentStateKey,
    queryFn: ({ signal }) => webchatApi.getAgentState(signal),
  });

  // Move focus into the dialog on open; Escape closes it.
  useEffect(() => {
    ref.current?.focus();
  }, []);

  const accept = useMutation({
    mutationFn: (item: ChatInboxItem) => webchatApi.accept(item.session_id),
    onSuccess: (res) => {
      setError(null);
      void qc.invalidateQueries({ queryKey: chatInboxKey });
      void qc.invalidateQueries({ queryKey: chatAgentStateKey });
      onClose();
      void navigate({ to: "/agent/tickets/$ticketId", params: { ticketId: String(res.ticket_id) } });
    },
    onError: (err) => {
      const code = chatErrorCode(err);
      setError(
        code === "at_capacity"
          ? t("webchat.agent.inbox.atCapacity", { count: stateQ.data?.max_concurrent ?? 0 })
          : code === "already_taken"
            ? t("webchat.agent.alreadyTaken")
            : code === "not_waiting"
              ? t("webchat.agent.notWaiting")
              : code === "forbidden"
                ? t("webchat.agent.forbidden")
                : t("webchat.agent.actionFailed"),
      );
      // The list is stale after a conflict.
      void qc.invalidateQueries({ queryKey: chatInboxKey });
    },
  });

  const waiting = inboxQ.data?.waiting ?? [];
  const mine = inboxQ.data?.mine ?? [];
  const bot = inboxQ.data?.bot ?? [];

  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="dialog"
      aria-label={t("webchat.agent.inbox.title")}
      data-testid="chat-inbox-panel"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
      className="absolute right-0 z-40 mt-1.5 w-80 max-w-[90vw] overflow-hidden rounded-lg border border-hairline bg-surface shadow-xl focus:outline-none"
    >
      <div className="border-b border-hairline px-3 py-2 text-[12px] font-semibold text-ink">
        {t("webchat.agent.inbox.title")}
      </div>
      <ChatAvailabilityForm />
      <section aria-labelledby="chat-waiting-heading" className="border-t border-hairline">
        <h3 id="chat-waiting-heading" className="px-3 pt-2 text-[12px] font-semibold text-muted">
          {t("webchat.agent.inbox.waiting")}
        </h3>
        {error && (
          <p role="alert" className="px-3 pt-1 text-[12px] text-danger" data-testid="chat-accept-error">
            {error}
          </p>
        )}
        {waiting.length === 0 ? (
          <p className="px-3 py-3 text-[12.5px] text-muted" data-testid="chat-waiting-empty">
            {t("webchat.agent.inbox.noneWaiting")}
          </p>
        ) : (
          <ul className="max-h-56 list-none overflow-y-auto" data-testid="chat-waiting-list">
            {waiting.map((item) => (
              <WaitingRow
                key={item.session_id}
                item={item}
                busy={accept.isPending}
                onAccept={(it) => accept.mutate(it)}
              />
            ))}
          </ul>
        )}
      </section>
      {bot.length > 0 && (
        <section aria-labelledby="chat-bot-heading" className="border-t border-hairline" data-testid="chat-bot-section">
          <h3 id="chat-bot-heading" className="px-3 pt-2 text-[12px] font-semibold text-muted">
            {t("webchat.agent.inbox.botSection", { count: bot.length })}
          </h3>
          <ul className="max-h-48 list-none overflow-y-auto" data-testid="chat-bot-list">
            {bot.map((item) => (
              <WaitingRow
                key={item.session_id}
                item={item}
                takeover
                busy={accept.isPending}
                onAccept={(it) => accept.mutate(it)}
              />
            ))}
          </ul>
        </section>
      )}
      <section aria-labelledby="chat-mine-heading" className="border-t border-hairline">
        <h3 id="chat-mine-heading" className="px-3 pt-2 text-[12px] font-semibold text-muted">
          {t("webchat.agent.inbox.mine")}
        </h3>
        {mine.length === 0 ? (
          <p className="px-3 py-3 text-[12.5px] text-muted" data-testid="chat-mine-empty">
            {t("webchat.agent.inbox.noneMine")}
          </p>
        ) : (
          <ul className="max-h-48 list-none overflow-y-auto" data-testid="chat-mine-list">
            {mine.map((item) => (
              <li key={item.session_id}>
                <Link
                  to="/agent/tickets/$ticketId"
                  params={{ ticketId: String(item.ticket_id) }}
                  onClick={onClose}
                  data-testid={`chat-mine-${item.session_id}`}
                  className="block px-3 py-2 text-[13px] text-ink hover:bg-surface-subtle"
                >
                  <span className="block truncate font-medium">
                    {item.visitor_name?.trim() || t("webchat.agent.visitorFallback")}
                  </span>
                  <span className="block truncate text-[11.5px] text-muted">
                    {[item.topic_label, item.tn].filter(Boolean).join(" · ")}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
