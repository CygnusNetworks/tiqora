import type { JSX } from "preact";
import { useEffect, useRef } from "preact/hooks";
import type { Translate } from "../i18n";
import { isLive, type ChatState } from "../state";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";

export type ChatHandlers = {
  onSend: (body: string) => void;
  onFile: (file: File) => void;
  onTyping: () => void;
  onRetry: (clientId: string) => void;
  onContact: (email: string) => Promise<string | null>;
  onEnd: () => void;
  onRestart: () => void;
  download: (seq: number, index: number) => Promise<Blob>;
};

function statusLine(s: ChatState, t: Translate): string {
  if (s.session && !isLive(s.session.status)) return t("status.ended");
  if (s.stream === "reconnecting") return t("status.reconnecting");
  if (s.session?.status === "active") {
    return s.session.agent_name ? t("status.active", { name: s.session.agent_name }) : t("status.activeAnon");
  }
  return t("status.waiting");
}

export function ChatView(props: { state: ChatState; t: Translate; h: ChatHandlers; ended: boolean }): JSX.Element {
  const { state, t, h } = props;
  const config = state.config;
  const restart = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (props.ended) restart.current?.focus();
  }, [props.ended]);
  return (
    <div class="tq-chat">
      <p class="tq-status" role="status">
        {statusLine(state, t)}
      </p>
      <MessageList
        messages={state.messages}
        pending={state.pending}
        t={t}
        agentTyping={state.agentTyping}
        needsContact={!!state.session?.needs_contact}
        onRetry={h.onRetry}
        onContact={h.onContact}
        download={h.download}
      />
      {state.error && (
        <p class="tq-err tq-banner" role="alert">
          {state.error}
        </p>
      )}
      {props.ended ? (
        <div class="tq-foot">
          <button
            class="tq-btn"
            type="button"
            onClick={h.onRestart}
            ref={restart}
          >
            {t("ended.restart")}
          </button>
        </div>
      ) : (
        config && (
          <div class="tq-foot">
            <Composer
              config={config}
              t={t}
              disabled={!state.session?.can_send}
              onSend={h.onSend}
              onFile={h.onFile}
              onTyping={h.onTyping}
            />
            <button class="tq-link tq-endbtn" type="button" onClick={h.onEnd}>
              {t("ended.end")}
            </button>
          </div>
        )
      )}
    </div>
  );
}
