/**
 * Draft model, client-side validation and payload building of the web-chat
 * admin editor. Rules and paths mirror `backend/src/tiqora/webchat/models.py`
 * and the R-ADMIN-422 error shape (`prechat_fields.2.key`, `allowed_origins.1`),
 * so server and client errors land on the same field.
 */
import i18n from "@/i18n";
import { ApiError } from "./api";
import type { ChatConfig, ChatConfigValues } from "./webchatApi";

export type ChatTopic = ChatConfigValues["topics"][number];
export type PrechatField = NonNullable<ChatConfigValues["prechat_fields"]>[number];
export type Values = Omit<Required<ChatConfigValues>, "upload_policy" | "theme" | "rate_limits"> & {
  upload_policy: Required<NonNullable<ChatConfigValues["upload_policy"]>>;
  theme: Required<NonNullable<ChatConfigValues["theme"]>>;
  rate_limits: Required<NonNullable<ChatConfigValues["rate_limits"]>>;
};

export const UPLOAD_TYPES = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "application/pdf",
  "text/plain",
] as const;

export const RATE_LIMIT_BOUNDS = {
  sessions_per_ip_10min: [1, 100],
  sessions_per_config_minute: [5, 300],
  messages_per_session_minute: [5, 300],
  messages_per_ip_minute: [5, 600],
  uploads_per_session_10min: [1, 100],
  offline_per_ip_hour: [1, 50],
  offline_per_config_hour: [1, 1000],
} as const satisfies Record<string, readonly [number, number]>;
export type RateLimitKey = keyof typeof RATE_LIMIT_BOUNDS;

export const RESERVED_FIELD_KEYS = ["message", "topic"] as const;
export const FIELD_KEY = /^[a-z][a-z0-9_]{0,39}$/;
export const TOPIC_KEY = /^[a-z][a-z0-9_-]{0,39}$/;
export const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
export const MAX_UPLOAD_MB = 25;
const MAX_TOPICS = 20;
const MAX_FIELDS = 15;
const MAX_OPTIONS = 50;
const MAX_ORIGINS = 20;

export type Draft = {
  name: string;
  valid: boolean;
  /** One website per line, exactly as typed. */
  originsText: string;
  values: Values;
};

export type FormErrors = Record<string, string>;

/** Validation message `webchat.admin.err.<key>` in the current UI language. */
function tr(key: string, params?: Record<string, unknown>): string {
  return i18n.t(`webchat.admin.err.${key}`, params);
}

export function setValues(draft: Draft, patch: Partial<Values>): Draft {
  return { ...draft, values: { ...draft.values, ...patch } };
}

export function defaultValues(): Values {
  return {
    allowed_origins: [],
    topics: [{ key: "", label: "", queue_id: 0, priority_id: null, ai_mode: "off" }],
    greeting: null,
    privacy_url: null,
    offline_mode: "form",
    business_calendar: null,
    wait_timeout_seconds: 180,
    prechat_fields: [
      { key: "name", kind: "text", label: "Name", required: false, options: [], maps_to: "name", dynamic_field: null },
      { key: "email", kind: "email", label: "E-Mail", required: false, options: [], maps_to: "email", dynamic_field: null },
    ],
    upload_policy: { enabled: true, max_bytes: 10 * 1024 * 1024, types: [...UPLOAD_TYPES] },
    theme: { primary_color: "#2563eb", position: "right", mode: "auto", launcher_label: null },
    rate_limits: {
      sessions_per_ip_10min: 5,
      sessions_per_config_minute: 30,
      messages_per_session_minute: 30,
      messages_per_ip_minute: 60,
      uploads_per_session_10min: 10,
      offline_per_ip_hour: 3,
      offline_per_config_hour: 60,
    },
    default_customer_user: null,
    show_agent_names: true,
    retention_days: 30,
    bot_name: "KI-Assistent",
    bot_tone_prompt: null,
    show_on_portal: false,
    identity_create_customers: false,
  };
}

/** Fills everything the API may omit so the editor always holds a complete object. */
export function completeValues(v: ChatConfigValues): Values {
  const d = defaultValues();
  return {
    ...d,
    ...v,
    allowed_origins: v.allowed_origins ?? d.allowed_origins,
    prechat_fields: (v.prechat_fields ?? d.prechat_fields).map((f) => ({
      ...f,
      options: f.options ?? [],
      dynamic_field: f.dynamic_field ?? null,
    })),
    upload_policy: { ...d.upload_policy, ...v.upload_policy },
    theme: { ...d.theme, ...v.theme },
    rate_limits: { ...d.rate_limits, ...v.rate_limits },
  } as Values;
}

export function newDraft(): Draft {
  return { name: "", valid: true, originsText: "", values: defaultValues() };
}

export function draftFromConfig(c: ChatConfig): Draft {
  const values = completeValues(c.values);
  return { name: c.name, valid: c.valid, originsText: values.allowed_origins.join("\n"), values };
}

export function originLines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l !== "");
}

/** Textarea line numbers (1-based) of the non-empty lines, in order. */
export function originLineNumbers(text: string): number[] {
  const out: number[] = [];
  text.split("\n").forEach((l, i) => {
    if (l.trim() !== "") out.push(i + 1);
  });
  return out;
}

export function slugifyTopicKey(label: string): string {
  const base = label
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const key = /^[a-z]/.test(base) ? base : `t-${base}`.replace(/-$/, "");
  return key.slice(0, 40).replace(/-+$/, "") || "topic";
}

/** Gives topics without a key a unique one derived from the label. */
export function withTopicKeys(topics: ChatTopic[]): ChatTopic[] {
  const used = new Set(topics.map((t) => t.key).filter((k) => k));
  return topics.map((t) => {
    if (t.key) return t;
    const base = slugifyTopicKey(t.label);
    let key = base;
    for (let n = 2; used.has(key); n++) key = `${base.slice(0, 36)}-${n}`;
    used.add(key);
    return { ...t, key };
  });
}

function boundMsg(min: number, max: number) {
  return tr("bound", { min, max });
}

function inRange(n: unknown, min: number, max: number): boolean {
  return typeof n === "number" && Number.isInteger(n) && n >= min && n <= max;
}

/** Same rules as the backend models; keys are the dotted paths the server uses. */
export function validateDraft(draft: Draft): FormErrors {
  const e: FormErrors = {};
  const v = draft.values;
  if (!draft.name.trim()) e.name = tr("nameRequired");
  else if (draft.name.trim().length > 200) e.name = tr("nameLong");

  if (originLines(draft.originsText).length > MAX_ORIGINS) {
    e.allowed_origins = tr("originsMax", { max: MAX_ORIGINS });
  }

  if (v.topics.length < 1) e.topics = tr("topicsMin");
  if (v.topics.length > MAX_TOPICS) e.topics = tr("topicsMax", { max: MAX_TOPICS });
  v.topics.forEach((t, i) => {
    const label = t.label.trim();
    if (!label) e[`topics.${i}.label`] = tr("topicLabelRequired");
    else if (label.length > 80) e[`topics.${i}.label`] = tr("topicLabelLong");
    if (!t.queue_id || t.queue_id < 1) e[`topics.${i}.queue_id`] = tr("queueRequired");
    if (t.key && !TOPIC_KEY.test(t.key)) e[`topics.${i}.key`] = tr("keyInvalid");
  });
  const topicKeys = withTopicKeys(v.topics.map((t) => ({ ...t, label: t.label.trim() }))).map((t) => t.key);
  topicKeys.forEach((k, i) => {
    if (topicKeys.indexOf(k) !== i) e[`topics.${i}.key`] = tr("keyDuplicate");
  });

  if ((v.greeting ?? "").length > 500) e.greeting = tr("greetingLong");
  const privacy = (v.privacy_url ?? "").trim();
  if (privacy) {
    if (privacy.length > 500) e.privacy_url = tr("urlLong");
    else if (!(privacy.startsWith("https://") || /^http:\/\/(localhost|127\.0\.0\.1)/.test(privacy))) {
      e.privacy_url = tr("urlHttps");
    }
  }

  if (!inRange(v.wait_timeout_seconds, 30, 3600)) e.wait_timeout_seconds = boundMsg(30, 3600);
  if (!inRange(v.retention_days, 1, 3650)) e.retention_days = boundMsg(1, 3650);
  if (!v.bot_name.trim()) e.bot_name = tr("botNameRequired");
  else if (v.bot_name.trim().length > 80) e.bot_name = tr("botNameLong");
  if ((v.bot_tone_prompt ?? "").length > 2000) e.bot_tone_prompt = tr("toneLong");
  if ((v.default_customer_user ?? "").length > 200) e.default_customer_user = tr("valueLong");

  if (!HEX_COLOR.test(v.theme.primary_color)) e["theme.primary_color"] = tr("colorInvalid");
  if ((v.theme.launcher_label ?? "").length > 40) e["theme.launcher_label"] = tr("max40");

  (Object.keys(RATE_LIMIT_BOUNDS) as RateLimitKey[]).forEach((k) => {
    const [min, max] = RATE_LIMIT_BOUNDS[k];
    if (!inRange(v.rate_limits[k], min, max)) e[`rate_limits.${k}`] = boundMsg(min, max);
  });
  if (!inRange(Math.round(v.upload_policy.max_bytes / 1024 / 1024), 1, MAX_UPLOAD_MB)) {
    e["upload_policy.max_bytes"] = boundMsg(1, MAX_UPLOAD_MB);
  }
  if (v.upload_policy.types.length < 1) e["upload_policy.types"] = tr("typesMin");

  Object.assign(e, validatePrechat(v.prechat_fields));
  return e;
}

/** R-PRECHAT-KEYS and the field rules; paths are relative to `prechat_fields`. */
export function validatePrechat(fields: PrechatField[]): FormErrors {
  const e: FormErrors = {};
  if (fields.length > MAX_FIELDS) e.prechat_fields = tr("fieldsMax", { max: MAX_FIELDS });
  const seen = new Map<string, number>();
  const mapped: Record<string, number> = { email: 0, name: 0 };
  fields.forEach((f, i) => {
    const p = `prechat_fields.${i}`;
    if (!FIELD_KEY.test(f.key)) {
      e[`${p}.key`] = tr("fieldKeyPattern");
    } else if ((RESERVED_FIELD_KEYS as readonly string[]).includes(f.key)) {
      e[`${p}.key`] = tr("fieldKeyReserved", { key: f.key });
    } else if (seen.has(f.key)) {
      e[`${p}.key`] = tr("keyDuplicate");
    }
    seen.set(f.key, i);
    const label = f.label.trim();
    if (!label) e[`${p}.label`] = tr("fieldLabelRequired");
    else if (label.length > 100) e[`${p}.label`] = tr("fieldLabelLong");

    const options = f.options ?? [];
    if (f.kind === "select" && options.length === 0) {
      e[`${p}.options`] = tr("optionsRequired");
    } else if (f.kind !== "select" && options.length > 0) {
      e[`${p}.options`] = tr("optionsOnlySelect");
    } else if (options.length > MAX_OPTIONS) {
      e[`${p}.options`] = tr("optionsMax", { max: MAX_OPTIONS });
    } else if (options.some((o) => !o.value.trim() || !o.label.trim())) {
      e[`${p}.options`] = tr("optionsIncomplete");
    } else if (new Set(options.map((o) => o.value)).size !== options.length) {
      e[`${p}.options`] = tr("optionsUnique");
    }

    if (f.maps_to === "email" || f.maps_to === "name") {
      mapped[f.maps_to] += 1;
      if (mapped[f.maps_to] > 1) {
        e[`${p}.maps_to`] = tr(f.maps_to === "email" ? "mapsEmailTwice" : "mapsNameTwice");
      } else if (f.maps_to === "email" && f.kind !== "email") {
        e[`${p}.maps_to`] = tr("mapsEmailKind");
      } else if (f.maps_to === "name" && f.kind !== "text") {
        e[`${p}.maps_to`] = tr("mapsNameKind");
      }
    }
    if (f.maps_to === "dynamic_field" && !f.dynamic_field) {
      e[`${p}.dynamic_field`] = tr("dynamicRequired");
    }
  });
  return e;
}

/** The payload the API takes: trimmed, empty strings as null, no stray option lists. */
export function buildValues(draft: Draft): Values {
  const v = draft.values;
  const nullIfEmpty = (s: string | null | undefined) => {
    const t = (s ?? "").trim();
    return t === "" ? null : t;
  };
  return {
    ...v,
    allowed_origins: originLines(draft.originsText),
    topics: withTopicKeys(v.topics.map((t) => ({ ...t, label: t.label.trim() }))),
    greeting: nullIfEmpty(v.greeting),
    privacy_url: nullIfEmpty(v.privacy_url),
    default_customer_user: nullIfEmpty(v.default_customer_user),
    bot_name: v.bot_name.trim(),
    bot_tone_prompt: nullIfEmpty(v.bot_tone_prompt),
    theme: { ...v.theme, launcher_label: nullIfEmpty(v.theme.launcher_label) },
    prechat_fields: v.prechat_fields.map((f) => ({
      key: f.key,
      kind: f.kind,
      label: f.label.trim(),
      required: !!f.required,
      options: f.kind === "select" ? (f.options ?? []).map((o) => ({ value: o.value.trim(), label: o.label.trim() })) : [],
      maps_to: f.maps_to ?? "none",
      dynamic_field: f.maps_to === "dynamic_field" ? (f.dynamic_field ?? null) : null,
    })),
  };
}

/** `{"detail":{"errors":{path: msg}}}` (R-ADMIN-422) -> map; `null` for any other error. */
export function serverFieldErrors(err: unknown): FormErrors | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const body = err.detail as { detail?: { errors?: unknown } } | null;
  const errors = body?.detail?.errors;
  if (!errors || typeof errors !== "object") return null;
  const out: FormErrors = {};
  for (const [k, v] of Object.entries(errors as Record<string, unknown>)) {
    if (typeof v === "string") out[k] = v;
  }
  return Object.keys(out).length ? out : null;
}

export type EditorTab = "general" | "hours" | "ai" | "prechat" | "look" | "protection" | "embed";

export function tabOfPath(path: string): EditorTab {
  const head = path.split(".")[0];
  if (head === "topics" && path.endsWith(".ai_mode")) return "ai";
  switch (head) {
    case "bot_name":
    case "bot_tone_prompt":
      return "ai";
    case "show_on_portal":
    case "identity_create_customers":
      return "embed";
    case "business_calendar":
    case "wait_timeout_seconds":
    case "offline_mode":
    case "default_customer_user":
      return "hours";
    case "prechat_fields":
      return "prechat";
    case "theme":
      return "look";
    case "rate_limits":
    case "upload_policy":
    case "retention_days":
      return "protection";
    default:
      return "general";
  }
}
