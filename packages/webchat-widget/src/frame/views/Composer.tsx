import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import type { ChatPublicConfig } from "../api";
import type { Translate } from "../i18n";
import { fmtBytes } from "../util";

const TYPING_EVERY_MS = 3000;

/** Client-side mirror of the server's upload check; returns a translation key or null. */
export function checkFile(file: File, upload: ChatPublicConfig["upload"]): { key: string; vars?: Record<string, string> } | null {
  if (!upload.enabled) return { key: "upload.disabled" };
  if (file.size === 0) return { key: "upload.empty" };
  if (file.size > upload.max_bytes) return { key: "upload.tooLarge", vars: { max: fmtBytes(upload.max_bytes) } };
  const ok = upload.types.some((x) => (x.endsWith("/*") ? file.type.startsWith(x.slice(0, -1)) : x === file.type));
  return ok ? null : { key: "upload.type" };
}

export function Composer(props: {
  config: ChatPublicConfig;
  t: Translate;
  disabled: boolean;
  onSend: (body: string) => void;
  onFile: (file: File) => void;
  onTyping: () => void;
}): JSX.Element {
  const { config, t } = props;
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const lastPing = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const max = config.max_message_length;
  const showCounter = text.length > max * 0.9;

  const send = () => {
    const body = text.trim();
    if (!body || props.disabled) return;
    if (body.length > max) {
      setError(t("composer.tooLong", { max: String(max) }));
      return;
    }
    setError(null);
    setText("");
    props.onSend(body);
  };

  const take = (file: File) => {
    const bad = checkFile(file, config.upload);
    if (bad) {
      setError(t(bad.key, bad.vars));
      return;
    }
    setError(null);
    props.onFile(file);
  };

  return (
    <div class="tq-composer">
      {error && (
        <p class="tq-err" id="tq-composer-err" role="alert">
          {error}
        </p>
      )}
      <div class="tq-row">
        {config.upload.enabled && (
          <>
            <button
              type="button"
              class="tq-icon"
              aria-label={t("composer.attach")}
              title={t("composer.attach")}
              disabled={props.disabled}
              onClick={() => fileInput.current?.click()}
            >
              {"\u{1F4CE}"}
            </button>
            <input
              ref={fileInput}
              type="file"
              hidden
              tabIndex={-1}
              accept={config.upload.types.join(",")}
              onChange={(e) => {
                const f = e.currentTarget.files?.[0];
                e.currentTarget.value = "";
                if (f) take(f);
              }}
            />
          </>
        )}
        <textarea
          class="tq-input"
          rows={1}
          value={text}
          disabled={props.disabled}
          aria-label={t("composer.placeholder")}
          aria-invalid={error ? "true" : undefined}
          aria-describedby={error ? "tq-composer-err" : undefined}
          placeholder={t("composer.placeholder")}
          onInput={(e) => {
            setText(e.currentTarget.value);
            if (error) setError(null);
            const now = Date.now();
            if (now - lastPing.current >= TYPING_EVERY_MS) {
              lastPing.current = now;
              props.onTyping();
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          onPaste={(e) => {
            const f = Array.from(e.clipboardData?.files ?? []).find((x) => x.type.startsWith("image/"));
            if (f && config.upload.enabled) {
              e.preventDefault();
              take(f);
            }
          }}
        />
        <button type="button" class="tq-btn tq-send" disabled={props.disabled || !text.trim()} onClick={send}>
          {t("composer.send")}
        </button>
      </div>
      {showCounter && (
        <p class={text.length > max ? "tq-count tq-err" : "tq-count"} aria-live="polite">
          {text.length} / {max}
        </p>
      )}
    </div>
  );
}
