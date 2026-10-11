import type { ArticleCreateRequest, TelegramButtonIn } from "@/lib/api";
import { api, ApiError } from "@/lib/api";
import { telegramChatKey, useTelegramChat } from "@/lib/telegramChatApi";
import { ticketChatKey, useTicketChat, webchatApi } from "@/lib/webchatApi";

/**
 * What differs between the messenger-style conversations the agent answers
 * from the shared composer: the outgoing channel, its limits, which extras
 * exist and where the meta data of the chat lives.
 */
export type MessengerChannelAdapter = {
  channel: "telegram" | "webchat";
  maxMessageLength: number;
  /** i18n key of the textarea's accessible name. */
  inputLabelKey: string;
  supportsButtons: boolean;
  supportsQuote: boolean;
  metaQueryKey: (ticketId: number) => readonly unknown[];
  sendTyping: (ticketId: number) => Promise<unknown>;
  extraPayload: (o: { quoteId: number | null; buttons: TelegramButtonIn[] }) => Partial<ArticleCreateRequest>;
  /** A hook: the name of the person on the other end (null while unknown). */
  useRecipientName: (ticketId: number) => string | null;
  /** A hook: the customer's language, for the button preset (Telegram only). */
  useCustomerLanguage?: (ticketId: number) => string | null;
  /** True for a refusal that means "this conversation is over". */
  isConversationEnded?: (err: unknown) => boolean;
};

export const telegramAdapter: MessengerChannelAdapter = {
  channel: "telegram",
  maxMessageLength: 4096,
  inputLabelKey: "ticket.telegram.composer.inputLabel",
  supportsButtons: true,
  supportsQuote: true,
  metaQueryKey: telegramChatKey,
  sendTyping: (id) => api.postTelegramTyping(id),
  extraPayload: ({ quoteId, buttons }) => ({
    telegram_reply_to_article_id: quoteId,
    ...(buttons.length > 0 ? { telegram_buttons: buttons } : {}),
  }),
  useRecipientName: (id) => {
    const chat = useTelegramChat(id, true).data ?? null;
    return chat?.display_name || chat?.username || null;
  },
  useCustomerLanguage: (id) => useTelegramChat(id, true).data?.customer_language ?? null,
};

export const webchatAdapter: MessengerChannelAdapter = {
  channel: "webchat",
  maxMessageLength: 4000,
  inputLabelKey: "webchat.agent.composer.inputLabel",
  supportsButtons: false,
  supportsQuote: false,
  metaQueryKey: ticketChatKey,
  sendTyping: (id) => webchatApi.typing(id),
  extraPayload: () => ({}),
  useRecipientName: (id) => useTicketChat(id, true).data?.visitor_name ?? null,
  isConversationEnded: (err) =>
    err instanceof ApiError &&
    err.status === 409 &&
    typeof err.detail === "object" &&
    err.detail !== null &&
    (err.detail as { detail?: unknown }).detail === "chat_not_live",
};
