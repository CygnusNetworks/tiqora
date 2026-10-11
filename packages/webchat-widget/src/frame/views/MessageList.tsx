import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ChatMessage } from "../api";
import type { Translate } from "../i18n";
import type { PendingMessage } from "../state";
import { ContactPrompt } from "./ContactPrompt";
import { MessageText } from "./MessageText";

type Attachment = ChatMessage["attachments"][number];
const RASTER = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

function AttachmentButton(props: {
  seq: number;
  a: Attachment;
  t: Translate;
  download: (seq: number, index: number) => Promise<Blob>;
}): JSX.Element {
  const { a, seq } = props;
  const [thumb, setThumb] = useState<string | null>(null);
  const raster = RASTER.has(a.content_type);
  useEffect(() => {
    if (!raster) return;
    let url: string | null = null;
    let dead = false;
    props.download(seq, a.index).then(
      (blob) => {
        if (dead) return;
        url = URL.createObjectURL(blob);
        setThumb(url);
      },
      () => undefined,
    );
    return () => {
      dead = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [seq, a.index, raster]);

  const save = async () => {
    try {
      const url = URL.createObjectURL(await props.download(seq, a.index));
      const link = document.createElement("a");
      link.href = url;
      link.download = a.filename;
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch {
      // download failed: nothing to show for a click that did nothing
    }
  };
  return (
    <button type="button" class="tq-att" onClick={save} title={props.t("upload.download")}>
      {thumb ? <img src={thumb} alt="" /> : null}
      <span>{a.filename}</span>
    </button>
  );
}

export function MessageList(props: {
  messages: ChatMessage[];
  pending: PendingMessage[];
  t: Translate;
  agentTyping: boolean;
  needsContact: boolean;
  onRetry: (clientId: string) => void;
  onContact: (email: string) => Promise<string | null>;
  download: (seq: number, index: number) => Promise<Blob>;
}): JSX.Element {
  const { t } = props;
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [props.messages.length, props.pending.length, props.agentTyping]);

  let lastNoMail = -1;
  props.messages.forEach((m, i) => {
    if (m.sender === "system" && m.code === "no_agent" && m.params?.has_email === false) lastNoMail = i;
  });

  return (
    <div class="tq-log" role="log" aria-live="polite" aria-relevant="additions" tabIndex={0}>
      {props.messages.map((m, i) => {
        if (m.sender === "system") {
          const code = m.code ?? m.body ?? "";
          let key = `system.${code}`;
          if (code === "no_agent" && m.params?.has_email === false) key = "system.no_agentNoEmail";
          if (code === "agent_joined" && !m.params?.name) key = "system.agent_joinedAnon";
          const line = t(key, { name: String(m.params?.name ?? "") });
          if (line === key) return null; // unknown code: nothing sensible to show
          return (
            <div key={m.seq}>
              <p class="tq-sys">{line}</p>
              {i === lastNoMail && props.needsContact && <ContactPrompt t={t} onSave={props.onContact} />}
            </div>
          );
        }
        const own = m.sender === "visitor";
        return (
          <div key={m.seq} class={own ? "tq-msg tq-own" : "tq-msg tq-other"}>
            {!own && <span class="tq-name">{m.agent_name || t("header.support")}</span>}
            <div class="tq-bubble">
              {m.body ? <MessageText text={m.body} /> : null}
              {m.attachments.map((a) => (
                <AttachmentButton key={a.index} seq={m.seq} a={a} t={t} download={props.download} />
              ))}
            </div>
          </div>
        );
      })}
      {props.pending.map((p) => (
        <div key={p.clientId} class="tq-msg tq-own tq-pending">
          <div class="tq-bubble">
            {p.body ? <MessageText text={p.body} /> : null}
            {p.file && <span class="tq-att">{p.file.name}</span>}
          </div>
          {p.status === "failed" && (
            <p class="tq-err">
              {t("composer.failed")}{" "}
              <button type="button" class="tq-link" onClick={() => props.onRetry(p.clientId)}>
                {t("composer.retry")}
              </button>
            </p>
          )}
        </div>
      ))}
      {props.agentTyping && (
        <p class="tq-typing" role="status">
          {t("typing")}
        </p>
      )}
      <div ref={end} />
    </div>
  );
}
