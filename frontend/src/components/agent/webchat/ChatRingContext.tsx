import { createContext, useContext, type ReactNode } from "react";
import { useChatRing } from "@/lib/chatRing";

type ChatRingState = ReturnType<typeof useChatRing>;

const NONE: ChatRingState = { pending: 0, topics: [], ready: false };
const ChatRingContext = createContext<ChatRingState>(NONE);

/**
 * Mounts `useChatRing()` once (the only owner of the ring context and the
 * inbox/topic queries) and hands its result to the header's chat button, so
 * `HeaderControls` needs no extra props.
 */
export function ChatRingProvider({ children }: { children: ReactNode }) {
  const value = useChatRing();
  return <ChatRingContext.Provider value={value}>{children}</ChatRingContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- tiny context hook next to its provider
export function useChatRingState(): ChatRingState {
  return useContext(ChatRingContext);
}
