import type { JSX } from "preact";
import type { Translate } from "../i18n";
import type { ChatState } from "../state";
import { ChatView, type ChatHandlers } from "./ChatView";

/** The transcript stays visible; the only action is a new chat. */
export function EndedView(props: { state: ChatState; t: Translate; h: ChatHandlers }): JSX.Element {
  return <ChatView {...props} ended />;
}
