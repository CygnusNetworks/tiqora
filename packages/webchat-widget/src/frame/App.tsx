import type { JSX } from "preact";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "preact/hooks";
import type { HostToFrame, PreviewConfig } from "../shared/protocol";
import { ChatApiError, type ChatMessage, type ChatPublicConfig, type createChatApi } from "./api";
import { createFrameBridge, type FrameBridge } from "./bridge";
import { isRtl, loadMessages, type Translate } from "./i18n";
import { SharedChatStream, type SharedStreamOptions } from "./shared-stream";
import { endReason, type SseEvent } from "./sse";
import { initial, isLive, maxSeq, reducer } from "./state";
import { clearSession, loadSession, saveSession, visitorId, type Store } from "./storage";
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

const PREVIEW_DEFAULT: ChatPublicConfig = {
  greeting: null,
  max_message_length: 4000,
  offline_mode: "form",
  online: true,
  prechat_fields: [],
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
    topics: p.topics.map((t) => ({ ...t, online: true })),
  };
}

/** What a stream `message` event may carry, normalised; null when unusable. */
function asMessage(data: string): ChatMessage | null {
  try {
    const o = JSON.parse(data) as Partial<ChatMessage> | null;
    if (!o || typeof o.seq !== "number" || !Number.isInteger(o.seq)) return null;
    if (o.sender !== "visitor" && o.sender !== "agent" && o.sender !== "system") return null;
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
  const seq = useRef(0);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mainRef = useRef<HTMLElement>(null);
  const readyPosted = useRef(false);
  const lastUnread = useRef(0);

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
      const stored = loadSession(params.k, props.store);
      if (stored) {
        try {
          const session = await api.session(stored.sessionId, stored.token);
          const all: ChatMessage[] = [];
          // The frame is reloaded on every host page: the transcript comes from the server.
          for (let after = 0, n = 0; n < 5; n++) {
            const page = await api.messages(stored.sessionId, stored.token, after);
            all.push(...page);
            if (page.length < 200) break;
            after = maxSeq(page);
          }
          if (dead) return;
          auth.current = { id: stored.sessionId, token: stored.token };
          seq.current = maxSeq(all); // resume from what was loaded; replays are deduped
          dispatch({ type: "session-restored", session, messages: all });
        } catch (e) {
          if (e instanceof ChatApiError && e.status === 404) clearSession(params.k, props.store);
          // other failures: show the pre-chat form, the stored session survives for the next load
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
      view === "chat" ? ".tq-input:not(:disabled)" : view === "ended" ? ".tq-foot .tq-btn" : view === "prechat" || view === "offline" ? "input,textarea,select" : ".tq-notice";
    mainRef.current?.querySelector<HTMLElement>(sel)?.focus();
  }, [state.open, view, t]);

  // --- persist a monotonic lastSeq (several tabs must never lower it) ---------------
  useEffect(() => {
    const top = maxSeq(state.messages);
    if (top > seq.current) seq.current = top;
    const a = auth.current;
    if (!live || !a || !top) return;
    const stored = loadSession(params.k, props.store);
    if (stored && stored.sessionId !== a.id) return; // another chat took over the slot
    saveSession(params.k, { sessionId: a.id, token: a.token, lastSeq: Math.max(stored?.lastSeq ?? 0, top) }, props.store);
  }, [state.messages, live, params.k, props.store]);

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
        if (!known && m.sender !== "visitor") bridgeRef.current?.post({ p: "tiqora-chat/1", type: "message", sender: m.sender });
      } else if (e.event === "session") {
        try {
          const o = JSON.parse(e.data) as { status?: unknown; agent_name?: unknown };
          if (typeof o.status === "string") {
            dispatch({ type: "session-event", status: o.status, agentName: typeof o.agent_name === "string" ? o.agent_name : null });
          }
        } catch {
          // malformed event: ignore
        }
      } else if (e.event === "typing") {
        dispatch({ type: "typing" });
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
    onRestart: () => {
      if (live) clearSession(params.k, props.store);
      auth.current = null;
      seq.current = 0;
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
      const created = await api.createSession({
        key: params.k,
        origin: params.origin,
        visitor_id: visitorId(props.store),
        message: v.message,
        prechat: v.prechat,
        topic: v.topic ?? stateRef.current.topic,
        language: params.lang,
        client_id: clientId,
      });
      auth.current = { id: created.session_id, token: created.token };
      saveSession(params.k, { sessionId: created.session_id, token: created.token, lastSeq: created.message.seq }, props.store);
      dispatch({ type: "session-created", created });
    } catch (e) {
      if (e instanceof ChatApiError && e.status === 422 && Object.keys(e.errors).length) setServerErrors(e.errors);
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
    formError,
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
