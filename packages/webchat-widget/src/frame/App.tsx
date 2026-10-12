import type { JSX } from "preact";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "preact/hooks";
import type { HostToFrame, PreviewConfig } from "../shared/protocol";
import { ChatApiError, type ChatMessage, type ChatPublicConfig, type createChatApi } from "./api";
import { createFrameBridge, type FrameBridge } from "./bridge";
import { isRtl, loadMessages, type Translate } from "./i18n";
import { SharedChatStream, type SharedStreamOptions } from "./shared-stream";
import { endReason, type SseEvent } from "./sse";
import { claimName, mayRestore } from "./identity";
import { canStartNew, contiguousSeq, initial, isLive, maxSeq, reducer } from "./state";
import { clearSession, loadSession, saveSession, visitorId, type StoredSession, type Store } from "./storage";
import { applyTheme } from "./theme";
import { errorText, newClientId } from "./util";
import { ChatView, type ChatHandlers } from "./views/ChatView";
import { EndedView } from "./views/EndedView";
import { OfflineForm } from "./views/OfflineForm";
import { PrechatForm, type PrechatValues } from "./views/PrechatForm";

export type FrameParams = { k: string; origin: string; lang: string; preview: boolean };
export type ChatApi = ReturnType<typeof createChatApi>;
type StreamLike = { start(): void; stop(): void };

const TYPING_VISIBLE_MS = 5000;
const PREVIEW_AGENT = "Max";
const PORTAL_REFRESH_MS = 9 * 60 * 1000;

const PREVIEW_DEFAULT: ChatPublicConfig = {
  bot_name: "KI-Assistent",
  greeting: null,
  max_message_length: 4000,
  offline_mode: "form",
  online: true,
  prechat_fields: [],
  portal_identity: false,
  privacy_url: null,
  theme: { mode: "auto", position: "right", primary_color: "#2563eb" },
  topics: [],
  upload: { enabled: true, max_bytes: 10 * 1024 * 1024, types: ["image/png", "image/jpeg"] },
};

function applyPreview(base: ChatPublicConfig, p: PreviewConfig | null): ChatPublicConfig {
  if (!p) return base;
  return {
    ...base,
    greeting: p.greeting,
    theme: { ...base.theme, ...p.theme },
    prechat_fields: p.prechat_fields,
    topics: p.topics.map((t) => ({ ...t, ai_first: false, online: true })),
  };
}

/** What a stream `message` event may carry, normalised; null when unusable. */
function asMessage(data: string): ChatMessage | null {
  try {
    const o = JSON.parse(data) as Partial<ChatMessage> | null;
    if (!o || typeof o.seq !== "number" || !Number.isInteger(o.seq)) return null;
    if (o.sender !== "visitor" && o.sender !== "agent" && o.sender !== "bot" && o.sender !== "system") return null;
    return {
      agent_name: null,
      body: null,
      client_id: null,
      code: null,
      created: "",
      ...o,
      attachments: Array.isArray(o.attachments) ? o.attachments : [],
      params: o.params && typeof o.params === "object" ? o.params : {},
    } as ChatMessage;
  } catch {
    return null;
  }
}

export function App(props: {
  params: FrameParams;
  api: ChatApi;
  t?: Translate;
  store?: Store;
  bridge?: FrameBridge;
  createStream?: (o: SharedStreamOptions) => StreamLike;
}): JSX.Element | null {
  const { params, api } = props;
  const [state, dispatch] = useReducer(reducer, initial);
  const [t, setT] = useState<Translate | null>(() => props.t ?? null);
  const [previewCfg, setPreviewCfg] = useState<PreviewConfig | null>(null);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const live = !params.preview;
  const dir = isRtl(params.lang) ? "rtl" : "ltr";

  const stateRef = useRef(state);
  stateRef.current = state;
  const auth = useRef<{ id: string; token: string } | null>(null);
  const bridgeRef = useRef<FrameBridge | null>(null);
  // Stream resume cursor (Last-Event-ID and the ended catch-up): the contiguous high-water
  // mark, never the max — a POST response must not move it past lines the stream missed.
  const seq = useRef(0);
  const seqFloor = useRef(0);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mainRef = useRef<HTMLElement>(null);
  const readyPosted = useRef(false);
  const lastUnread = useRef(0);
  const aliveRef = useRef(true);
  // The signed token: memory only, never stored (C9). `deferred` is a stored signed-in chat
  // waiting for the host to vouch for the visitor before it is restored.
  const identRef = useRef<string | null>(null);
  // Portal assertions live 600 s on the server: refetch one that is older than 9 minutes.
  const portalRef = useRef<{ at: number } | null>(null);
  const deferred = useRef<StoredSession | null>(null);
  const restoreRef = useRef<(s: StoredSession) => Promise<void>>(async () => undefined);
  const identifyRef = useRef<(token: string) => Promise<void>>(async () => undefined);
  const logoutRef = useRef<() => void>(() => undefined);

  const config = useMemo(
    () => (state.config ? (params.preview ? applyPreview(state.config, previewCfg) : state.config) : null),
    [state.config, previewCfg, params.preview],
  );

  useEffect(() => () => void (typingTimer.current && clearTimeout(typingTimer.current)), []);

  // --- translations ---------------------------------------------------------------
  useEffect(() => {
    if (props.t) return;
    let dead = false;
    void loadMessages(params.lang).then((fn) => !dead && setT(() => fn));
    return () => {
      dead = true;
    };
  }, [params.lang, props.t]);

  // --- bridge ---------------------------------------------------------------------
  useEffect(() => {
    const onHost = (m: HostToFrame) => {
      if (m.type === "open") dispatch({ type: "opened" });
      else if (m.type === "close") dispatch({ type: "closed" });
      else if (m.type === "set-topic") dispatch({ type: "set-topic", topic: m.topic });
      else if (m.type === "preview-config" && params.preview) setPreviewCfg(m.config);
      else if (m.type === "identify" && !params.preview) void identifyRef.current(m.token);
      else if (m.type === "logout" && !params.preview) logoutRef.current();
    };
    const b = props.bridge ?? createFrameBridge({ hostOrigin: params.origin, onHostMessage: onHost });
    bridgeRef.current = b;
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") b.post({ p: "tiqora-chat/1", type: "close-request" });
    };
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("keydown", esc);
      if (!props.bridge) b.dispose();
    };
  }, [params.origin, params.preview, props.bridge]);

  // --- theme ----------------------------------------------------------------------
  useEffect(() => {
    if (config) applyTheme(document.documentElement, { primaryColor: config.theme.primary_color, mode: config.theme.mode }, dir);
  }, [config, dir]);

  // --- identity ---------------------------------------------------------------------
  const setIdentity = useCallback((token: string, name?: string | null) => {
    identRef.current = token;
    if (portalRef.current) portalRef.current.at = Date.now();
    dispatch({ type: "identity-set", name: name ?? claimName(token) });
  }, []);
  const dropIdentity = useCallback(() => {
    identRef.current = null;
    dispatch({ type: "identity-cleared" });
  }, []);
  const freshPortal = useCallback(async () => {
    const p = portalRef.current;
    if (!p || !identRef.current || Date.now() - p.at < PORTAL_REFRESH_MS) return;
    try {
      const r = await api.portalIdentity({ key: params.k, origin: params.origin, visitor_id: visitorId(props.store) });
      identRef.current = r.identity;
      p.at = Date.now();
    } catch {
      // keep the old one: the server decides (a retry or the 422 path follows)
    }
  }, [api, params.k, params.origin, props.store]);
  const toStart = useCallback(() => {
    // unconditional: a deferred signed-in chat has neither auth nor a session yet (C9 logout)
    clearSession(params.k, props.store);
    auth.current = null;
    seq.current = 0;
    seqFloor.current = 0;
    deferred.current = null;
    dispatch({ type: "reset", force: true });
  }, [params.k, props.store]);

  /** Attach the pending identity to the running chat (204 also for the same one again). */
  const attachIdentity = useCallback(
    async (token: string) => {
      const a = auth.current;
      if (!a) return;
      try {
        await api.identify(a.id, a.token, token);
        const session = await api.session(a.id, a.token);
        if (session) dispatch({ type: "session-refreshed", session });
        bridgeRef.current?.post({ p: "tiqora-chat/1", type: "identity", verified: !!session?.identified_as });
      } catch (e) {
        if (!(e instanceof ChatApiError)) return;
        if (e.code === "identity_conflict") toStart(); // the chat belongs to somebody else
        else if (e.code === "invalid_identity") {
          dropIdentity();
          bridgeRef.current?.post({ p: "tiqora-chat/1", type: "identity", verified: false });
        }
      }
    },
    [api, toStart, dropIdentity],
  );

  const restoreStored = async (stored: StoredSession, isDead: () => boolean) => {
    try {
      // Ownership first: nothing of the stored chat is fetched or shown before the server
      // confirms that the signed-in visitor may have it (a shared browser).
      let identified = false;
      if (identRef.current) {
        await freshPortal();
        try {
          await api.identify(stored.sessionId, stored.token, identRef.current!);
          identified = true;
        } catch (e) {
          if (!(e instanceof ChatApiError)) throw e;
          if (e.code === "identity_conflict") {
            toStart();
            return;
          }
          if (e.code === "invalid_identity") {
            dropIdentity();
            bridgeRef.current?.post({ p: "tiqora-chat/1", type: "identity", verified: false });
          } else if (e.status === 404) throw e;
          else if (stored.identified) return; // cannot prove ownership: show nothing
        }
      }
      const session = await api.session(stored.sessionId, stored.token);
      const all: ChatMessage[] = [];
      // The frame is reloaded on every host page: the transcript comes from the server.
      for (let after = 0, n = 0; n < 5; n++) {
        const page = await api.messages(stored.sessionId, stored.token, after);
        all.push(...page);
        if (page.length < 200) break;
        after = maxSeq(page);
      }
      if (isDead()) return;
      auth.current = { id: stored.sessionId, token: stored.token };
      // the pages are a full read up to their top: resume from there; replays are deduped
      seqFloor.current = maxSeq(all);
      seq.current = seqFloor.current;
      dispatch({ type: "session-restored", session, messages: all });
      if (identified) bridgeRef.current?.post({ p: "tiqora-chat/1", type: "identity", verified: !!session.identified_as });
    } catch (e) {
      if (e instanceof ChatApiError && e.status === 404) clearSession(params.k, props.store);
      // other failures: show the pre-chat form, the stored session survives for the next load
    }
  };
  restoreRef.current = (s) => restoreStored(s, () => !aliveRef.current);
  identifyRef.current = async (token) => {
    setIdentity(token);
    if (auth.current && stateRef.current.session && isLive(stateRef.current.session.status)) await attachIdentity(token);
  };
  logoutRef.current = () => {
    dropIdentity();
    toStart();
  };
  useEffect(() => {
    aliveRef.current = true;
    return () => void (aliveRef.current = false);
  }, []);

  // A signed-in chat from an earlier page view is restored once the visitor opens the chat
  // and the host has signed the visitor in by then; otherwise it is dropped.
  useEffect(() => {
    const d = deferred.current;
    if (!d || !state.open) return;
    deferred.current = null;
    if (identRef.current) void restoreRef.current(d);
    else clearSession(params.k, props.store);
  }, [state.open, params.k, props.store]);

  // --- start: config, then a stored session ---------------------------------------
  useEffect(() => {
    let dead = false;
    void (async () => {
      let cfg: ChatPublicConfig;
      try {
        cfg = await api.config(params.k, params.origin);
      } catch (e) {
        if (dead) return;
        if (!params.preview) {
          dispatch({ type: "config-failed", status: e instanceof ChatApiError ? e.status : 0 });
          return;
        }
        cfg = PREVIEW_DEFAULT; // draft or disabled config: the admin still sees the preview
      }
      if (dead) return;
      if (params.preview) {
        dispatch({ type: "config-loaded", config: cfg });
        dispatch({ type: "opened" });
        return;
      }
      // Own-origin portal: the logged-in customer counts as a fresh identify for the restore rule.
      // Preview frames never ask (the admin browser's portal identity must not attach).
      if (cfg.portal_identity) {
        try {
          const p = await api.portalIdentity({ key: params.k, origin: params.origin, visitor_id: visitorId(props.store) });
          if (dead) return;
          portalRef.current = { at: Date.now() };
          setIdentity(p.identity, p.name);
        } catch {
          // 404 and every failure: anonymous
        }
      }
      const stored = loadSession(params.k, props.store);
      if (stored) {
        if (!mayRestore(stored, !!identRef.current)) {
          // The host calls identify() only after `ready`; the decision waits for the visitor to open the chat.
          deferred.current = stored;
        } else {
          await restoreStored(stored, () => dead);
        }
      }
      if (!dead) dispatch({ type: "config-loaded", config: cfg });
    })();
    return () => {
      dead = true;
    };
  }, [api, params.k, params.origin, params.preview, props.store]);

  // --- ready: only after /config succeeded (the loader keeps the launcher hidden) ---
  useEffect(() => {
    if (!state.config || !t || readyPosted.current || state.view === "loading" || state.view === "blocked") return;
    readyPosted.current = true;
    const th = state.config.theme;
    bridgeRef.current?.post({
      p: "tiqora-chat/1",
      type: "ready",
      online: state.config.online,
      hidden: state.view === "hidden",
      launcherLabel: (th.launcher_label || t("launcher.default")).slice(0, 100),
      theme: { primaryColor: th.primary_color, position: th.position, mode: th.mode },
      dir,
    });
  }, [state.config, state.view, t, dir]);

  useEffect(() => {
    if (state.unread !== lastUnread.current) {
      lastUnread.current = state.unread;
      bridgeRef.current?.post({ p: "tiqora-chat/1", type: "unread", count: Math.min(state.unread, 9999) });
    }
  }, [state.unread]);

  // --- focus follows the open panel and the view (never while closed) ----------------
  const view = state.view;
  useEffect(() => {
    if (!state.open || !t) return;
    const sel =
      view === "chat" ? ".tq-input:not(:disabled)" : view === "ended" ? ".tq-foot .tq-btn,.tq-foot .tq-notice" : view === "prechat" || view === "offline" ? "input,textarea,select" : ".tq-notice";
    mainRef.current?.querySelector<HTMLElement>(sel)?.focus();
  }, [state.open, view, t]);

  // --- persist a monotonic lastSeq (several tabs must never lower it) ---------------
  useEffect(() => {
    const top = maxSeq(state.messages);
    seq.current = Math.max(seq.current, contiguousSeq(state.messages, seqFloor.current));
    const a = auth.current;
    if (!live || !a || !top) return;
    const stored = loadSession(params.k, props.store);
    if (stored && stored.sessionId !== a.id) return; // another chat took over the slot
    saveSession(params.k, { sessionId: a.id, token: a.token, lastSeq: Math.max(stored?.lastSeq ?? 0, top), identified: stored?.identified ?? false }, props.store);
  }, [state.messages, live, params.k, props.store]);

  // The server says who the chat belongs to: remember it, so a reload needs a fresh sign-in.
  const identifiedAs = state.session?.identified_as ?? null;
  useEffect(() => {
    const a = auth.current;
    if (!live || !a || !identifiedAs) return;
    const stored = loadSession(params.k, props.store);
    if (stored && stored.sessionId === a.id && !stored.identified) saveSession(params.k, { ...stored, identified: true }, props.store);
  }, [identifiedAs, live, params.k, props.store]);

  // --- live stream ------------------------------------------------------------------
  const sessionId = state.session?.session_id ?? null;
  const sessionLive = !!state.session && isLive(state.session.status);
  const onStreamEvent = useCallback(
    (e: SseEvent) => {
      if (e.event === "message") {
        const m = asMessage(e.data);
        if (!m) return;
        const known = stateRef.current.messages.some((x) => x.seq === m.seq);
        dispatch({ type: "message", message: m });
        if (!known && m.sender !== "visitor") {
          const sender = m.sender === "system" ? "system" : m.sender === "bot" ? "bot" : "agent";
          bridgeRef.current?.post({ p: "tiqora-chat/1", type: "message", sender });
        }
      } else if (e.event === "session") {
        try {
          const o = JSON.parse(e.data) as { status?: unknown; agent_name?: unknown; bot_name?: unknown };
          if (typeof o.status === "string") {
            dispatch({
              type: "session-event",
              status: o.status,
              agentName: typeof o.agent_name === "string" ? o.agent_name : null,
              botName: typeof o.bot_name === "string" ? o.bot_name : null,
            });
          }
        } catch {
          // malformed event: ignore
        }
      } else if (e.event === "typing") {
        let who: "agent" | "bot" = "agent";
        try {
          if ((JSON.parse(e.data) as { who?: unknown } | null)?.who === "bot") who = "bot";
        } catch {
          // a plain typing ping
        }
        dispatch({ type: "typing", who });
        if (typingTimer.current) clearTimeout(typingTimer.current);
        typingTimer.current = setTimeout(() => dispatch({ type: "typing-expired" }), TYPING_VISIBLE_MS);
      } else if (e.event === "end") {
        void endReason(e.data); // any reason (known or not) is a plain end
        dispatch({ type: "end" });
      }
    },
    [],
  );
  useEffect(() => {
    const a = auth.current;
    if (!live || !sessionId || !sessionLive || !a) return;
    const make = props.createStream ?? ((o: SharedStreamOptions) => new SharedChatStream(o));
    const stream = make({
      sessionId,
      url: api.streamUrl(sessionId),
      token: a.token,
      lastSeq: () => seq.current,
      onEvent: onStreamEvent,
      onState: (s) => dispatch({ type: "stream-state", state: s }),
    });
    stream.start();
    return () => stream.stop();
  }, [live, sessionId, sessionLive, api, onStreamEvent, props.createStream]);

  // After an end: catch up on late lines (e.g. the no_agent line) and learn needs_contact.
  const ended = state.view === "ended";
  useEffect(() => {
    const a = auth.current;
    if (!live || !ended || !a) return;
    let dead = false;
    void (async () => {
      try {
        const page = await api.messages(a.id, a.token, seq.current);
        if (!dead && Array.isArray(page)) page.forEach((message) => dispatch({ type: "message", message }));
        const session = await api.session(a.id, a.token);
        if (!dead && session) dispatch({ type: "session-refreshed", session });
      } catch {
        // best effort
      }
    })();
    return () => {
      dead = true;
    };
  }, [ended, live, api]);

  // --- actions ----------------------------------------------------------------------
  const fail = useCallback(
    (e: unknown, clientId?: string) => {
      if (clientId) dispatch({ type: "pending-failed", clientId });
      if (e instanceof ChatApiError && e.code === "disabled") dispatch({ type: "end" });
      if (t) dispatch({ type: "error", error: errorText(t, e, stateRef.current.config?.upload.max_bytes) });
    },
    [t],
  );

  const deliver = useCallback(
    async (p: { clientId: string; body: string; file?: File }) => {
      const a = auth.current;
      const cur = stateRef.current;
      dispatch({ type: "pending", pending: { ...p, status: "sending" } });
      dispatch({ type: "error", error: null });
      if (!live) {
        const n = maxSeq(cur.messages) + 1;
        const base = { agent_name: null, attachments: [], code: null, created: "", params: {}, client_id: p.clientId };
        dispatch({ type: "message", message: { ...base, seq: n, sender: "visitor", body: p.body || p.file?.name || null } });
        return;
      }
      if (!a) return;
      try {
        const m = p.file ? await api.upload(a.id, a.token, p.file, p.clientId) : await api.send(a.id, a.token, p.body, p.clientId);
        dispatch({ type: "message", message: m });
      } catch (e) {
        fail(e, p.clientId);
      }
    },
    [api, live, fail],
  );

  const handlers: ChatHandlers = {
    onSend: (body) => void deliver({ clientId: newClientId(), body }),
    onFile: (file) => void deliver({ clientId: newClientId(), body: "", file }),
    onTyping: () => {
      const a = auth.current;
      if (live && a) api.typing(a.id, a.token).catch(() => undefined);
    },
    onRetry: (clientId) => {
      const p = stateRef.current.pending.find((x) => x.clientId === clientId);
      if (p) void deliver(p);
    },
    onContact: async (email) => {
      const a = auth.current;
      if (!live || !a) return null;
      try {
        await api.contact(a.id, a.token, email, null);
        return null;
      } catch (e) {
        return t ? errorText(t, e) : "";
      }
    },
    onEnd: () => {
      const a = auth.current;
      dispatch({ type: "end" });
      if (live && a) api.end(a.id, a.token).catch((e) => fail(e));
    },
    onHandoff: () => {
      const a = auth.current;
      if (!live || !a || stateRef.current.handoffBusy) return;
      dispatch({ type: "handoff-started" });
      api.handoff(a.id, a.token).then(
        () => undefined, // the session event (waiting / ended) removes the button
        async (e: unknown) => {
          dispatch({ type: "handoff-failed" });
          if (e instanceof ChatApiError && e.code === "not_bot") {
            // an agent was faster: learn the new status
            try {
              dispatch({ type: "session-refreshed", session: await api.session(a.id, a.token) });
            } catch {
              // the stream tells us
            }
          } else fail(e);
        },
      );
    },
    onRestart: () => {
      if (!canStartNew(stateRef.current.config)) return;
      if (live) clearSession(params.k, props.store);
      auth.current = null;
      seq.current = 0;
      seqFloor.current = 0;
      dispatch({ type: "reset" });
    },
    download: async (s, i) => {
      const a = auth.current;
      if (!live || !a) throw new Error("no download");
      return api.download(a.id, a.token, s, i);
    },
  };

  const start = async (v: PrechatValues) => {
    setServerErrors({});
    setFormError(null);
    const clientId = newClientId();
    if (!live) {
      const base = { agent_name: null, attachments: [], code: null, created: "", params: {} };
      dispatch({
        type: "session-created",
        created: { session_id: "preview", status: "active", token: "", message: { ...base, client_id: clientId, seq: 1, sender: "visitor", body: v.message } },
      });
      dispatch({ type: "session-event", status: "active", agentName: PREVIEW_AGENT });
      dispatch({ type: "message", message: { ...base, client_id: null, seq: 2, sender: "agent", agent_name: PREVIEW_AGENT, body: t?.("preview.reply") ?? "" } });
      return;
    }
    setBusy(true);
    try {
      await freshPortal();
      const created = await api.createSession({
        key: params.k,
        origin: params.origin,
        visitor_id: visitorId(props.store),
        message: v.message,
        prechat: v.prechat,
        topic: v.topic ?? stateRef.current.topic,
        language: params.lang,
        client_id: clientId,
        ...(identRef.current ? { identity: identRef.current } : {}),
      });
      auth.current = { id: created.session_id, token: created.token };
      saveSession(params.k, { sessionId: created.session_id, token: created.token, lastSeq: created.message.seq, identified: !!identRef.current }, props.store);
      dispatch({ type: "session-created", created });
      if (identRef.current) {
        // who the server recognised (identified_as) is only on the session
        try {
          const session = await api.session(created.session_id, created.token);
          if (session) dispatch({ type: "session-refreshed", session });
          bridgeRef.current?.post({ p: "tiqora-chat/1", type: "identity", verified: !!session?.identified_as });
        } catch {
          // best effort: the next restore / refresh learns it
        }
      }
    } catch (e) {
      if (e instanceof ChatApiError && e.status === 422 && e.code === "invalid_identity") {
        dropIdentity();
        dispatch({ type: "identity-invalid" });
        bridgeRef.current?.post({ p: "tiqora-chat/1", type: "identity", verified: false });
      } else if (e instanceof ChatApiError && e.status === 422 && Object.keys(e.errors).length) setServerErrors(e.errors);
      else if (t) setFormError(errorText(t, e));
    } finally {
      setBusy(false);
    }
  };

  const sendOffline = async (v: PrechatValues) => {
    setServerErrors({});
    setFormError(null);
    if (!live) {
      dispatch({ type: "offline-sent" });
      return;
    }
    const emailField = state.config?.prechat_fields.find((f) => f.kind === "email")?.key ?? "email";
    setBusy(true);
    try {
      await api.offline({
        key: params.k,
        origin: params.origin,
        visitor_id: visitorId(props.store),
        message: v.message,
        email: typeof v.prechat[emailField] === "string" ? String(v.prechat[emailField]) : "",
        name: typeof v.prechat.name === "string" ? v.prechat.name : null,
        prechat: v.prechat,
        topic: v.topic ?? stateRef.current.topic,
        language: params.lang,
      });
      dispatch({ type: "offline-sent" });
    } catch (e) {
      if (e instanceof ChatApiError && e.status === 422 && Object.keys(e.errors).length) {
        // the synthetic email field is keyed "email"; a configured one keeps its own key
        setServerErrors(emailField !== "email" && e.errors.email ? { ...e.errors, [emailField]: e.errors.email } : e.errors);
      } else if (t) setFormError(errorText(t, e));
    } finally {
      setBusy(false);
    }
  };

  // --- render -----------------------------------------------------------------------
  if (!t || state.view === "loading" || state.view === "hidden") return <div class="tq-root" dir={dir} />;
  const formProps = {
    t,
    topic: state.topic ?? config?.topics.find((x) => x.online)?.key ?? null,
    onTopic: (topic: string) => dispatch({ type: "set-topic", topic }),
    busy,
    serverErrors,
    formError: formError ?? (state.identityError ? t("identity.invalid") : null),
  };
  let body: JSX.Element;
  switch (state.view) {
    case "blocked":
      body = (
        <p class="tq-notice" role="alert" tabIndex={-1}>
          {state.error === "403" ? t("error.notAllowed") : t("error.unavailable")}
        </p>
      );
      break;
    case "offline":
      body = config ? <OfflineForm {...formProps} config={config} onSubmit={(v) => void sendOffline(v)} /> : <div />;
      break;
    case "offline-sent":
      body = (
        <p class="tq-notice" role="status" tabIndex={-1}>
          {t("offline.thanks")}
        </p>
      );
      break;
    case "prechat":
      body = config ? (
        <PrechatForm
          {...formProps}
          config={config}
          title={t("prechat.title")}
          intro={config.greeting}
          identity={state.identity}
          submitLabel={t("prechat.start")}
          onSubmit={(v) => void start(v)}
        />
      ) : (
        <div />
      );
      break;
    case "ended":
      body = <EndedView state={state} t={t} h={handlers} />;
      break;
    default:
      body = <ChatView state={state} t={t} h={handlers} ended={false} />;
  }
  return (
    <div class="tq-root" dir={dir}>
      <header class="tq-head">
        <h1>{t("header.support")}</h1>
        <button
          type="button"
          class="tq-icon"
          aria-label={t("header.close")}
          onClick={() => bridgeRef.current?.post({ p: "tiqora-chat/1", type: "close-request" })}
        >
          {"✕"}
        </button>
      </header>
      <main class="tq-main" ref={mainRef}>{body}</main>
    </div>
  );
}
