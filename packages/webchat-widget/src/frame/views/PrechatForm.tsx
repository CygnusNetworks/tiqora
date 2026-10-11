import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ChatPublicConfig } from "../api";
import type { Translate } from "../i18n";
import { safeHref } from "../linkify";

type Field = ChatPublicConfig["prechat_fields"][number];
export type PrechatValues = {
  prechat: Record<string, string | boolean>;
  message: string;
  topic: string | null;
};

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MAX_TEXT = 200;
const MAX_TEXTAREA = 2000;

/** Server error code → translation key. */
function codeKey(code: string): string {
  switch (code) {
    case "email":
      return "prechat.invalidEmail";
    case "option":
      return "prechat.invalidOption";
    case "too_long":
      return "prechat.tooLong";
    case "type":
      return "error.generic";
    default:
      return "prechat.required";
  }
}

export function PrechatForm(props: {
  config: ChatPublicConfig;
  fields?: Field[];
  t: Translate;
  title: string;
  intro?: string | null;
  submitLabel: string;
  topic: string | null;
  onTopic: (key: string) => void;
  busy: boolean;
  serverErrors: Record<string, string>;
  formError: string | null;
  onSubmit: (v: PrechatValues) => void;
}): JSX.Element {
  const { config, t } = props;
  const fields = props.fields ?? config.prechat_fields;
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [message, setMessage] = useState("");
  const [local, setLocal] = useState<Record<string, string>>({});
  const root = useRef<HTMLFormElement>(null);
  const showTopic = config.topics.length > 1;
  const errors: Record<string, string> = { ...props.serverErrors, ...local };
  const privacy = safeHref(config.privacy_url);

  // Server errors arrive after a submit: move focus to the first invalid control.
  useEffect(() => {
    if (Object.keys(props.serverErrors).length) {
      root.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus();
    }
  }, [props.serverErrors]);

  const set = (key: string, v: string | boolean) => {
    setValues((o) => ({ ...o, [key]: v }));
    setLocal((o) => {
      const { [key]: _drop, ...rest } = o;
      return rest;
    });
  };

  const submit = (ev: Event) => {
    ev.preventDefault();
    const errs: Record<string, string> = {};
    const out: Record<string, string | boolean> = {};
    for (const f of fields) {
      const v = values[f.key];
      if (f.kind === "checkbox") {
        if (f.required && v !== true) errs[f.key] = "prechat.required";
        if (v === true || v === false) out[f.key] = v;
        continue;
      }
      const s = typeof v === "string" ? v.trim() : "";
      if (!s) {
        if (f.required) errs[f.key] = "prechat.required";
        continue;
      }
      if (f.kind === "email" && !EMAIL.test(s)) errs[f.key] = "prechat.invalidEmail";
      else if (f.kind === "select" && !f.options.some((o) => o.value === s)) errs[f.key] = "prechat.invalidOption";
      else if (s.length > (f.kind === "textarea" ? MAX_TEXTAREA : MAX_TEXT)) errs[f.key] = "prechat.tooLong";
      else out[f.key] = s;
    }
    const msg = message.trim();
    if (!msg) errs.message = "prechat.required";
    else if (msg.length > config.max_message_length) errs.message = "prechat.tooLong";
    setLocal(errs);
    if (Object.keys(errs).length) {
      // Focus after the re-render has set aria-invalid.
      setTimeout(() => root.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus(), 0);
      return;
    }
    props.onSubmit({ prechat: out, message: msg, topic: showTopic ? props.topic : null });
  };

  const errMsg = (key: string): string | null => {
    const e = errors[key];
    if (!e) return null;
    return t(e.includes(".") ? e : codeKey(e));
  };
  const a11y = (key: string) => ({
    id: `tq-f-${key}`,
    "aria-invalid": errors[key] ? ("true" as const) : undefined,
    "aria-describedby": errors[key] ? `tq-e-${key}` : undefined,
  });
  const err = (key: string) =>
    errors[key] ? (
      <p class="tq-err" id={`tq-e-${key}`}>
        {errMsg(key)}
      </p>
    ) : null;

  return (
    <form class="tq-form" ref={root} onSubmit={submit} noValidate>
      <h2>{props.title}</h2>
      {props.intro && <p class="tq-intro">{props.intro}</p>}
      {fields.map((f) => (
        <div class="tq-field" key={f.key}>
          {f.kind === "checkbox" ? (
            <label class="tq-check">
              <input
                type="checkbox"
                {...a11y(f.key)}
                checked={values[f.key] === true}
                onChange={(e) => set(f.key, e.currentTarget.checked)}
              />
              <span>{f.label}</span>
            </label>
          ) : (
            <>
              <label for={`tq-f-${f.key}`}>
                {f.label}
                {f.required ? " *" : ""}
              </label>
              {f.kind === "textarea" ? (
                <textarea
                  {...a11y(f.key)}
                  rows={3}
                  value={String(values[f.key] ?? "")}
                  onInput={(e) => set(f.key, e.currentTarget.value)}
                />
              ) : f.kind === "select" ? (
                <select
                  {...a11y(f.key)}
                  value={String(values[f.key] ?? "")}
                  onChange={(e) => set(f.key, e.currentTarget.value)}
                >
                  <option value="" />
                  {f.options.map((o) => (
                    <option value={o.value}>{o.label}</option>
                  ))}
                </select>
              ) : (
                <input
                  {...a11y(f.key)}
                  type={f.kind === "email" ? "email" : "text"}
                  autocomplete={f.kind === "email" ? "email" : f.key === "name" ? "name" : "off"}
                  value={String(values[f.key] ?? "")}
                  onInput={(e) => set(f.key, e.currentTarget.value)}
                />
              )}
            </>
          )}
          {err(f.key)}
        </div>
      ))}
      {showTopic && (
        <div class="tq-field">
          <label for="tq-f-topic">{t("prechat.topic")}</label>
          <select
            id="tq-f-topic"
            value={props.topic ?? ""}
            onChange={(e) => props.onTopic(e.currentTarget.value)}
          >
            {config.topics.map((tp) => (
              <option value={tp.key} disabled={!tp.online}>
                {tp.label}
              </option>
            ))}
          </select>
        </div>
      )}
      <div class="tq-field">
        <label for="tq-f-message">{t("prechat.message")} *</label>
        <textarea
          {...a11y("message")}
          rows={4}
          value={message}
          onInput={(e) => {
            setMessage(e.currentTarget.value);
            setLocal((o) => {
              const { message: _m, ...rest } = o;
              return rest;
            });
          }}
        />
        {err("message")}
      </div>
      {privacy && (
        <p class="tq-privacy">
          <a href={privacy} target="_blank" rel="noopener noreferrer">
            {t("prechat.privacy")}
          </a>
        </p>
      )}
      {props.formError && (
        <p class="tq-err" role="alert">
          {props.formError}
        </p>
      )}
      <button class="tq-btn" type="submit" disabled={props.busy}>
        {props.submitLabel}
      </button>
    </form>
  );
}
