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
  /** Who is typing: the AI (`bot`) or a human (`agent`). */
  typingBot: boolean;
  /** A signed-in visitor (host token or portal) is pending; the token itself stays in memory in App. */
  identity: { name: string | null } | null;
  /** The last sign-in was refused (422 `invalid_identity`): the full form shows with a message. */
  identityError: boolean;
  /** The handover request is running (the button is disabled). */
  handoffBusy: boolean;
};

export type Action =
  | { type: "config-loaded"; config: ChatPublicConfig }
  | { type: "config-failed"; status: number }
  | { type: "session-restored"; session: ChatSession; messages: ChatMessage[] }
  | { type: "session-created"; created: ChatSessionCreated }
  | { type: "message"; message: ChatMessage }
  | { type: "session-event"; status: string; agentName: string | null; botName?: string | null }
  | { type: "typing"; who?: "agent" | "bot" }
  | { type: "identity-set"; name: string | null }
  | { type: "identity-cleared" }
  | { type: "identity-invalid" }
  | { type: "handoff-started" }
  | { type: "handoff-failed" }
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
  typingBot: false,
  identity: null,
  identityError: false,
  handoffBusy: false,
};

/** Whether a new chat (pre-chat or offline form) can be offered; under `offline_mode: hide`
 * with nobody online there is nothing to show, so an ended chat keeps its transcript. */
export const canStartNew = (c: ChatPublicConfig | null) =>
  !!c && (c.online || c.offline_mode === "form" || (c.offline_mode === "ai" && c.topics.some((t) => t.ai_first)));

/** The view of a fresh start: chat form, offline form or nothing. */
export const startView = (c: ChatPublicConfig): View =>
  c.online || (c.offline_mode === "ai" && c.topics.some((t) => t.ai_first))
    ? "prechat"
    : c.offline_mode === "form"
      ? "offline"
      : "hidden";

const LIVE = ["bot", "waiting", "active"];
export const isLive = (status: string) => LIVE.includes(status);

/** Highest seq in a list (0 when empty). */
export const maxSeq = (messages: readonly ChatMessage[]) =>
  messages.reduce((m, x) => (x.seq > m ? x.seq : m), 0);

/**
 * The stream resume point: the largest S such that every seq in (floor, S] is known.
 * seq is gapless per session (one row-locked allocator), so a hole means a line the frame
 * has not seen yet (e.g. an agent reply sent while the stream was down, overtaken by the
 * POST response of the visitor's own message); resuming above it would skip it. `floor` is
 * a seq up to which a full server read already covered everything (the restore).
 */
export function contiguousSeq(messages: readonly ChatMessage[], floor = 0): number {
  let s = floor;
  for (const m of messages) {
    // messages are sorted by seq (merge keeps them so)
    if (m.seq === s + 1) s = m.seq;
    else if (m.seq > s + 1) break;
  }
  return s;
}

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
  const agentSpoke = add.some((m) => m.sender === "agent" || m.sender === "bot");
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
        view = startView(c);
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
        can_handoff: c.status === "bot",
        can_send: true,
        identified_as: null,
        bot_name: null,
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
        session: {
          ...s.session,
          status: a.status,
          agent_name: a.agentName,
          bot_name: a.status === "bot" ? (a.botName ?? s.session.bot_name ?? null) : null,
          can_handoff: a.status === "bot",
          can_send: live,
        },
        view: live ? s.view : "ended",
        agentTyping: live ? s.agentTyping : false,
        handoffBusy: false,
        pending: live ? s.pending : [],
      };
    }
    case "typing": {
      const bot = a.who === "bot";
      return s.agentTyping && s.typingBot === bot ? s : { ...s, agentTyping: true, typingBot: bot };
    }
    case "typing-expired":
      return s.agentTyping ? { ...s, agentTyping: false } : s;
    case "identity-set":
      return { ...s, identity: { name: a.name }, identityError: false };
    case "identity-cleared":
      return { ...s, identity: null };
    case "identity-invalid":
      return { ...s, identity: null, identityError: true };
    case "handoff-started":
      return { ...s, handoffBusy: true };
    case "handoff-failed":
      return { ...s, handoffBusy: false };
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
      if (!canStartNew(s.config)) return s; // never an empty panel (T16 M11)
      return {
        ...initial,
        config: s.config,
        view: s.config ? startView(s.config) : "hidden",
        open: s.open,
        identity: s.identity,
        topic: s.topic,
      };
  }
}
