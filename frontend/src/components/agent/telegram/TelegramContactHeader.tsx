import { useTranslation } from "react-i18next";
import type { TelegramChatOut } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";

function initialsOf(name: string): string {
  const words = name.replace(/^@/, "").trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return (words[0] ?? "?").slice(0, 2).toUpperCase();
}

/** Who the agent is chatting with, above a Telegram ticket's conversation:
 * the Telegram identity plus the trust/consent state the bot recorded. */
export function TelegramContactHeader({ chat, locale }: { chat: TelegramChatOut; locale: string }) {
  const { t } = useTranslation();
  const handle = chat.username ? `@${chat.username}` : null;
  const name = chat.display_name || handle || String(chat.chat_id);

  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-hairline bg-surface px-3 py-2"
      data-testid="telegram-contact-header"
    >
      <Avatar initials={initialsOf(name)} tone="customer" size={32} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate text-sm font-semibold text-ink" data-testid="telegram-contact-name">
            {name}
          </span>
          <Badge tone="accent">✈ Telegram</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
          {chat.display_name && handle && <span>{handle}</span>}
          {chat.identity_verified && chat.customer_user_login && (
            <span className="font-mono">{chat.customer_user_login}</span>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={chat.identity_verified ? "success" : "warn"} data-testid="telegram-identity">
          {chat.identity_verified
            ? t("ticket.telegram.identityVerified")
            : t("ticket.telegram.identityUnverified")}
        </Badge>
        {chat.consent_time && (
          <Badge
            tone="muted"
            data-testid="telegram-consent"
            title={formatDateTime(chat.consent_time, locale)}
          >
            {t("ticket.telegram.consentGiven")}
          </Badge>
        )}
        {chat.ai_escalated_at && (
          <Badge
            tone="accent"
            data-testid="telegram-ai-escalated"
            title={formatDateTime(chat.ai_escalated_at, locale)}
          >
            {t("ticket.telegram.aiEscalated")}
          </Badge>
        )}
      </div>
    </div>
  );
}
