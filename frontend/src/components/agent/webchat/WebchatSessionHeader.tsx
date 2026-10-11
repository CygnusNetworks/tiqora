import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { MAX_LINK_CHARS, safePageLink } from "./pageLink";
import { chatInboxKey, ticketChatKey, useTicketChat, webchatApi } from "@/lib/webchatApi";

function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return (words[0] ?? "?").slice(0, 2).toUpperCase();
}

function errorCode(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.detail === null || typeof err.detail !== "object") return null;
  const d = (err.detail as { detail?: unknown }).detail;
  return typeof d === "string" ? d : null;
}

/** Waiting time in whole units, ticking every 15 s. */
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

/**
 * Who the agent is chatting with, above a web chat ticket's conversation:
 * the visitor (as typed — plain text only), the page they are on, the state
 * of the session and the accept / end buttons. Renders nothing for a ticket
 * without a chat session.
 */
export function WebchatSessionHeader({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const chat = useTicketChat(ticketId, true).data ?? null;
  const age = useAgeSeconds(chat?.status === "waiting" ? chat.waiting_since : null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ticketChatKey(ticketId) });
    void qc.invalidateQueries({ queryKey: ["tickets", ticketId] });
    void qc.invalidateQueries({ queryKey: chatInboxKey });
  };

  const accept = useMutation({
    mutationFn: (sid: string) => webchatApi.accept(sid),
    onMutate: () => setMessage(null),
    onSuccess: refresh,
    onError: (err) => {
      const code = errorCode(err);
      setMessage(
        code === "already_taken"
          ? t("webchat.agent.alreadyTaken")
          : code === "at_capacity"
            ? t("webchat.agent.atCapacity")
            : t("webchat.agent.actionFailed"),
      );
      // The inbox and this header must show who has the chat now.
      refresh();
    },
  });
  const end = useMutation({
    mutationFn: (v: { sid: string; closeTicket: boolean }) => webchatApi.end(v.sid, v.closeTicket),
    onMutate: () => setMessage(null),
    onSuccess: refresh,
    onError: () => {
      setMessage(t("webchat.agent.actionFailed"));
      refresh();
    },
  });

  if (!chat) return null;

  const name = chat.visitor_name?.trim() || t("webchat.agent.visitorFallback");
  const link = safePageLink(chat.page_url);
  const pageText = chat.page_url ? chat.page_url.slice(0, MAX_LINK_CHARS) : null;

  const ageLabel =
    age === null
      ? ""
      : age < 60
        ? t("webchat.agent.ageSeconds", { count: age })
        : age < 3600
          ? t("webchat.agent.ageMinutes", { count: Math.floor(age / 60) })
          : t("webchat.agent.ageHours", { count: Math.floor(age / 3600) });
  const statusLabel =
    chat.status === "waiting"
      ? ageLabel
        ? t("webchat.agent.statusWaiting", { age: ageLabel })
        : t("webchat.agent.statusWaitingNoAge")
      : chat.status === "active"
        ? chat.assigned_name
          ? t("webchat.agent.statusActive", { name: chat.assigned_name })
          : t("webchat.agent.statusActiveNoName")
        : t("webchat.agent.statusEnded");
  const statusTone = chat.status === "waiting" ? "warn" : chat.status === "active" ? "success" : "muted";

  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-hairline bg-surface px-3 py-2"
      data-testid="webchat-session-header"
    >
      <Avatar initials={initialsOf(name)} tone="customer" size={32} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span
            aria-hidden
            data-testid="webchat-online-dot"
            data-online={chat.visitor_online ? "true" : "false"}
            title={chat.visitor_online ? t("webchat.agent.online") : t("webchat.agent.offline")}
            className={`h-2 w-2 shrink-0 rounded-full ${chat.visitor_online ? "bg-green" : "bg-muted/50"}`}
          />
          <span className="truncate text-sm font-semibold text-ink" data-testid="webchat-visitor-name">
            {name}
          </span>
          <Badge tone="accent">💬 {t("webchat.agent.badge")}</Badge>
          {chat.visitor_typing && (
            <span className="text-xs italic text-muted" data-testid="webchat-visitor-typing">
              {t("webchat.agent.typing")}
            </span>
          )}
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
          {chat.visitor_email && (
            <span className="inline-flex items-center gap-1.5">
              <span data-testid="webchat-visitor-email">{chat.visitor_email}</span>
              {!chat.visitor_email_verified && (
                <Badge tone="warn" data-testid="webchat-email-unverified">
                  {t("webchat.agent.emailUnverified")}
                </Badge>
              )}
            </span>
          )}
          {chat.topic_label && <span data-testid="webchat-topic">{chat.topic_label}</span>}
          {link ? (
            <a
              href={link.href}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="webchat-page-link"
              className="min-w-0 truncate text-accent hover:underline"
            >
              {link.label}
            </a>
          ) : (
            pageText && <span data-testid="webchat-page-text">{pageText}</span>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={statusTone} data-testid="webchat-status">
          {statusLabel}
        </Badge>
        {chat.status === "waiting" && (
          <Button
            size="sm"
            variant="primary"
            data-testid="webchat-accept"
            disabled={accept.isPending}
            onClick={() => accept.mutate(chat.session_id)}
          >
            {t("webchat.agent.accept")}
          </Button>
        )}
        {chat.status === "active" && (
          <Menu
            align="right"
            trigger={({ ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="webchat-end"
                disabled={end.isPending}
                className="inline-flex items-center gap-1 rounded-md border border-hairline px-2 py-1 text-xs font-medium text-ink hover:bg-surface-subtle disabled:opacity-50"
                {...toggleProps}
              >
                {t("webchat.agent.end")} ▾
              </button>
            )}
          >
            <MenuItem
              testId="webchat-end-keep"
              onSelect={() => end.mutate({ sid: chat.session_id, closeTicket: false })}
            >
              {t("webchat.agent.endKeepOpen")}
            </MenuItem>
            <MenuItem
              testId="webchat-end-close"
              onSelect={() => end.mutate({ sid: chat.session_id, closeTicket: true })}
            >
              {t("webchat.agent.endClose")}
            </MenuItem>
          </Menu>
        )}
      </div>
      {message && (
        <p role="alert" className="basis-full text-xs text-danger" data-testid="webchat-header-message">
          {message}
        </p>
      )}
    </div>
  );
}
