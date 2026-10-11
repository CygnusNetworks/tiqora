/** Pure view/state reducer of the chat frame: every view decision lives here. */
import type { ChatMessage, ChatPublicConfig, ChatSession, ChatSessionCreated } from "./api";
import type { StreamState } from "./sse";

export type View =
  | "loading"
  | "blocked"
  | "hidden"
  | "offline"
  | "offline-sent"
  | "prechat"
  | "chat"
  | "ended";

export type PendingMessage = {
  clientId: string;
  body: string;
  file?: File;
  status: "sending" | "failed";
};

export type ChatState = {
  view: View;
  config: ChatPublicConfig | null;
  session: ChatSession | null;
  messages: ChatMessage[];
  pending: PendingMessage[];
  agentTyping: boolean;
  stream: StreamState;
  error: string | null;
  topic: string | null;
  unread: number;
  open: boolean;
};

export type Action =
  | { type: "config-loaded"; config: ChatPublicConfig }
  | { type: "config-failed"; status: number }
  | { type: "session-restored"; session: ChatSession; messages: ChatMessage[] }
  | { type: "session-created"; created: ChatSessionCreated }
  | { type: "message"; message: ChatMessage }
  | { type: "session-event"; status: string; agentName: string | null }
  | { type: "typing" }
  | { type: "typing-expired" }
  | { type: "pending"; pending: PendingMessage }
  | { type: "pending-failed"; clientId: string }
  | { type: "end" }
  | { type: "opened" }
  | { type: "closed" }
  | { type: "offline-sent" }
  | { type: "set-topic"; topic: string }
  | { type: "stream-state"; state: StreamState }
  | { type: "error"; error: string | null }
  | { type: "session-refreshed"; session: ChatSession }
  | { type: "reset" };

export const initial: ChatState = {
  view: "loading",
  config: null,
  session: null,
  messages: [],
  pending: [],
  agentTyping: false,
  stream: "connecting",
  error: null,
  topic: null,
  unread: 0,
  open: false,
};

const LIVE = ["waiting", "active"];
export const isLive = (status: string) => LIVE.includes(status);

/** Highest seq in a list (0 when empty). */
export const maxSeq = (messages: readonly ChatMessage[]) =>
  messages.reduce((m, x) => (x.seq > m ? x.seq : m), 0);

/** Merge by seq: a known seq is kept (dedupe), the result is sorted. */
function merge(have: ChatMessage[], add: ChatMessage[]): ChatMessage[] {
  const seen = new Set(have.map((m) => m.seq));
  const fresh = add.filter((m) => !seen.has(m.seq) && seen.add(m.seq));
  return fresh.length ? [...have, ...fresh].sort((a, b) => a.seq - b.seq) : have;
}

function dropPending(pending: PendingMessage[], add: ChatMessage[]): PendingMessage[] {
  const ids = new Set(add.map((m) => m.client_id).filter(Boolean));
  return ids.size ? pending.filter((p) => !ids.has(p.clientId)) : pending;
}

function withMessages(s: ChatState, add: ChatMessage[]): ChatState {
  const messages = merge(s.messages, add);
  const isNew = messages !== s.messages;
  const incoming = isNew
    ? add.filter((m) => m.sender !== "visitor" && !s.messages.some((x) => x.seq === m.seq)).length
    : 0;
  const agentSpoke = add.some((m) => m.sender === "agent");
  return {
    ...s,
    messages,
    pending: dropPending(s.pending, add),
    agentTyping: agentSpoke && isNew ? false : s.agentTyping,
    unread: s.open ? 0 : s.unread + incoming,
  };
}

export function reducer(s: ChatState, a: Action): ChatState {
  switch (a.type) {
    case "config-loaded": {
      const c = a.config;
      let view = s.view;
      if (!s.session) {
        view = c.online ? "prechat" : c.offline_mode === "form" ? "offline" : "hidden";
      }
      const topic = s.topic && c.topics.some((t) => t.key === s.topic) ? s.topic : null;
      return { ...s, config: c, view, error: null, topic };
    }
    case "config-failed":
      return { ...s, view: a.status === 404 ? "hidden" : "blocked", error: String(a.status) };
    case "session-restored":
      return {
        ...withMessages({ ...s, session: a.session }, a.messages),
        unread: 0,
        view: isLive(a.session.status) ? "chat" : "ended",
      };
    case "session-created": {
      const c = a.created;
      const session: ChatSession = {
        agent_name: null,
        can_send: true,
        last_seq: c.message.seq,
        needs_contact: false,
        session_id: c.session_id,
        status: c.status,
        topic_label: null,
      };
      return {
        ...withMessages({ ...s, session, view: "chat", error: null }, [c.message]),
        unread: 0,
      };
    }
    case "message":
      return withMessages(s, [a.message]);
    case "session-event": {
      if (!s.session) return s;
      const live = isLive(a.status);
      return {
        ...s,
        session: { ...s.session, status: a.status, agent_name: a.agentName, can_send: live },
        view: live ? s.view : "ended",
        agentTyping: live ? s.agentTyping : false,
        pending: live ? s.pending : [],
      };
    }
    case "typing":
      return s.agentTyping ? s : { ...s, agentTyping: true };
    case "typing-expired":
      return s.agentTyping ? { ...s, agentTyping: false } : s;
    case "pending":
      return { ...s, pending: [...s.pending.filter((p) => p.clientId !== a.pending.clientId), a.pending] };
    case "pending-failed":
      return {
        ...s,
        pending: s.pending.map((p) => (p.clientId === a.clientId ? { ...p, status: "failed" } : p)),
      };
    case "end":
      return {
        ...s,
        view: s.session ? "ended" : s.view,
        session: s.session ? { ...s.session, can_send: false } : s.session,
        agentTyping: false,
        pending: [],
      };
    case "opened":
      return { ...s, open: true, unread: 0 };
    case "closed":
      return { ...s, open: false };
    case "offline-sent":
      return { ...s, view: "offline-sent" };
    case "set-topic":
      // before /config only remembered; config-loaded drops a topic the config lacks
      return !s.config || s.config.topics.some((t) => t.key === a.topic) ? { ...s, topic: a.topic } : s;
    case "stream-state":
      return s.stream === a.state ? s : { ...s, stream: a.state };
    case "error":
      return { ...s, error: a.error };
    case "session-refreshed":
      return s.session ? { ...s, session: a.session } : s;
    case "reset":
      return {
        ...initial,
        config: s.config,
        view: s.config?.online ? "prechat" : s.config?.offline_mode === "form" ? "offline" : "hidden",
        open: s.open,
        topic: s.topic,
      };
  }
}
