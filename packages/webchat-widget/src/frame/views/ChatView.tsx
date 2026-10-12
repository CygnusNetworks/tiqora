import type { JSX } from "preact";
import type { Translate } from "../i18n";
import { canStartNew, isLive, type ChatState } from "../state";
import { Composer } from "./Composer";
import { HandoffButton } from "./HandoffButton";
import { MessageList } from "./MessageList";

export type ChatHandlers = {
  onSend: (body: string) => void;
  onFile: (file: File) => void;
  onTyping: () => void;
  onRetry: (clientId: string) => void;
  onContact: (email: string) => Promise<string | null>;
  onEnd: () => void;
  onHandoff: () => void;
  onRestart: () => void;
  download: (seq: number, index: number) => Promise<Blob>;
};

export const botName = (s: ChatState) => s.session?.bot_name || s.config?.bot_name || "";

function statusLine(s: ChatState, t: Translate): string {
  if (s.session && !isLive(s.session.status)) return t("status.ended");
  if (s.stream === "reconnecting") return t("status.reconnecting");
  if (s.session?.status === "bot") return t("bot.status", { name: botName(s) });
  if (s.session?.status === "active") {
    return s.session.agent_name ? t("status.active", { name: s.session.agent_name }) : t("status.activeAnon");
  }
  return t("status.waiting");
}

export function ChatView(props: { state: ChatState; t: Translate; h: ChatHandlers; ended: boolean }): JSX.Element {
  const { state, t, h } = props;
  const config = state.config;
  return (
    <div class="tq-chat">
      <p class="tq-status" role="status">
        {statusLine(state, t)}
      </p>
      {(state.session?.identified || state.session?.identified_as) && (
        <p class="tq-signed">
          {state.session.identified_as
            ? t("identity.signedIn", { name: state.session.identified_as })
            : t("identity.signedInAnon")}
        </p>
      )}
      <MessageList
        botName={botName(state)}
        typingBot={state.typingBot}
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
          {canStartNew(config) ? (
            <button class="tq-btn" type="button" onClick={h.onRestart}>
              {t("ended.restart")}
            </button>
          ) : (
            <p class="tq-notice" tabIndex={-1}>{t("offline.title")}</p>
          )}
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
            {state.session?.can_handoff && state.session.status === "bot" && (
              <HandoffButton t={t} busy={state.handoffBusy} onClick={h.onHandoff} />
            )}
            <button class="tq-link tq-endbtn" type="button" onClick={h.onEnd}>
              {t("ended.end")}
            </button>
          </div>
        )
      )}
    </div>
  );
}
