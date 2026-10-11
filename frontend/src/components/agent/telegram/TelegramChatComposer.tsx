import { MessengerComposer } from "../messenger/MessengerComposer";
import { telegramAdapter } from "../messenger/adapters";

/** The messenger composer wired to the Telegram gateway. */
export function TelegramChatComposer(props: {
  ticketId: number;
  /** Presence heartbeat: true while there is something unsent. */
  onComposingChange?: (composing: boolean) => void;
}) {
  return <MessengerComposer {...props} adapter={telegramAdapter} />;
}
