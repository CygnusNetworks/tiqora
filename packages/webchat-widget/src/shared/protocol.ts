/**
 * postMessage protocol between the host-page loader and the chat iframe (C9).
 *
 * Both sides check `event.origin`, `event.source` AND the shape below before acting on
 * a message, and always post with an explicit target origin (never `"*"`). Message
 * content never crosses to the host page — only "a message arrived".
 *
 * The guards are strict: exact key sets at message level, value types, enums, bounded
 * strings and arrays. Anything else is dropped silently by the receiver.
 */

export const PROTOCOL = "tiqora-chat/1" as const;

export type FrameTheme = {
  primaryColor: string;
  position: "right" | "left";
  mode: "auto" | "light" | "dark";
};

/** Theme as the admin edits it (`ChatTheme` in backend webchat/models.py). */
export type PreviewTheme = {
  primary_color: string;
  position: "right" | "left";
  mode: "auto" | "light" | "dark";
  launcher_label: string | null;
};

export type PreviewField = {
  key: string;
  kind: "text" | "textarea" | "email" | "select" | "checkbox";
  label: string;
  required: boolean;
  options: { value: string; label: string }[];
};

/**
 * What the admin preview (`WidgetPreview`, Task 21) sends with `preview-config`: the
 * render-relevant subset of `ChatConfigValues`. Only honoured by a frame loaded with
 * `preview=1` from Tiqora's own origin. Extra keys inside the objects are tolerated
 * (the admin may pass its full values object) but never read.
 */
export type PreviewConfig = {
  theme: PreviewTheme;
  greeting: string | null;
  prechat_fields: PreviewField[];
  topics: { key: string; label: string }[];
};

type Msg<T extends string, B = object> = { p: typeof PROTOCOL; type: T } & B;

export type FrameToHost =
  | Msg<
      "ready",
      {
        online: boolean;
        hidden: boolean;
        launcherLabel: string;
        theme: FrameTheme;
        dir: "ltr" | "rtl";
      }
    >
  | Msg<"availability", { online: boolean }>
  | Msg<"close-request">
  | Msg<"unread", { count: number }>
  | Msg<"message", { sender: "agent" | "bot" | "system" }>
  | Msg<"identity", { verified: boolean }>;

export type HostToFrame =
  | Msg<"open">
  | Msg<"close">
  | Msg<"set-topic", { topic: string }>
  | Msg<"preview-config", { config: PreviewConfig }>
  | Msg<"identify", { token: string }>
  | Msg<"logout">;

// Same patterns/limits as backend webchat/models.py.
export const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
export const TOPIC_KEY = /^[a-z][a-z0-9_-]{0,39}$/;
const FIELD_KEY = /^[a-z][a-z0-9_]{0,39}$/;
const MAX_LABEL = 100;
const MAX_LAUNCHER_LABEL = 100;
const MAX_GREETING = 500;
const MAX_FIELDS = 15;
const MAX_TOPICS = 20;
const MAX_OPTIONS = 50;
const MAX_UNREAD = 9999;
const MAX_TOKEN = 4096;

/** Shape of a host-signed identity token (compact JWT); the frame/server verify the signature. */
export const IDENTITY_TOKEN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

export function isIdentityToken(v: unknown): v is string {
  return typeof v === "string" && v.length <= MAX_TOKEN && IDENTITY_TOKEN.test(v);
}

type Rec = Record<string, unknown>;

function isRecord(v: unknown): v is Rec {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function hasExactKeys(v: Rec, keys: readonly string[]): boolean {
  const own = Object.keys(v);
  return own.length === keys.length && keys.every((k) => Object.prototype.hasOwnProperty.call(v, k));
}

function isBool(v: unknown): v is boolean {
  return typeof v === "boolean";
}

function isStr(v: unknown, max: number, min = 0): v is string {
  return typeof v === "string" && v.length >= min && v.length <= max;
}

function oneOf<T extends string>(v: unknown, values: readonly T[]): v is T {
  return typeof v === "string" && (values as readonly string[]).includes(v);
}

const POSITIONS = ["right", "left"] as const;
const MODES = ["auto", "light", "dark"] as const;
const FIELD_KINDS = ["text", "textarea", "email", "select", "checkbox"] as const;

function isFrameTheme(v: unknown): v is FrameTheme {
  return (
    isRecord(v) &&
    hasExactKeys(v, ["primaryColor", "position", "mode"]) &&
    typeof v.primaryColor === "string" &&
    HEX_COLOR.test(v.primaryColor) &&
    oneOf(v.position, POSITIONS) &&
    oneOf(v.mode, MODES)
  );
}

function isPreviewTheme(v: unknown): v is PreviewTheme {
  return (
    isRecord(v) &&
    typeof v.primary_color === "string" &&
    HEX_COLOR.test(v.primary_color) &&
    oneOf(v.position, POSITIONS) &&
    oneOf(v.mode, MODES) &&
    (v.launcher_label === null ||
      v.launcher_label === undefined ||
      isStr(v.launcher_label, 40))
  );
}

function isOption(v: unknown): v is { value: string; label: string } {
  return isRecord(v) && isStr(v.value, MAX_LABEL, 1) && isStr(v.label, MAX_LABEL, 1);
}

function isPreviewField(v: unknown): v is PreviewField {
  return (
    isRecord(v) &&
    typeof v.key === "string" &&
    FIELD_KEY.test(v.key) &&
    oneOf(v.kind, FIELD_KINDS) &&
    isStr(v.label, MAX_LABEL, 1) &&
    isBool(v.required) &&
    Array.isArray(v.options) &&
    v.options.length <= MAX_OPTIONS &&
    v.options.every(isOption)
  );
}

function isPreviewTopic(v: unknown): v is { key: string; label: string } {
  return (
    isRecord(v) && typeof v.key === "string" && TOPIC_KEY.test(v.key) && isStr(v.label, 80, 1)
  );
}

export function isPreviewConfig(v: unknown): v is PreviewConfig {
  return (
    isRecord(v) &&
    isPreviewTheme(v.theme) &&
    (v.greeting === null || isStr(v.greeting, MAX_GREETING)) &&
    Array.isArray(v.prechat_fields) &&
    v.prechat_fields.length <= MAX_FIELDS &&
    v.prechat_fields.every(isPreviewField) &&
    Array.isArray(v.topics) &&
    v.topics.length <= MAX_TOPICS &&
    v.topics.every(isPreviewTopic)
  );
}

function header(data: unknown): Rec | null {
  return isRecord(data) && data.p === PROTOCOL && typeof data.type === "string" ? data : null;
}

/** Strict check of a message the loader received from the frame. */
export function isFrameToHost(data: unknown): data is FrameToHost {
  const m = header(data);
  if (!m) return false;
  switch (m.type) {
    case "ready":
      return (
        hasExactKeys(m, ["p", "type", "online", "hidden", "launcherLabel", "theme", "dir"]) &&
        isBool(m.online) &&
        isBool(m.hidden) &&
        isStr(m.launcherLabel, MAX_LAUNCHER_LABEL) &&
        isFrameTheme(m.theme) &&
        oneOf(m.dir, ["ltr", "rtl"] as const)
      );
    case "availability":
      return hasExactKeys(m, ["p", "type", "online"]) && isBool(m.online);
    case "close-request":
      return hasExactKeys(m, ["p", "type"]);
    case "unread":
      return (
        hasExactKeys(m, ["p", "type", "count"]) &&
        typeof m.count === "number" &&
        Number.isInteger(m.count) &&
        m.count >= 0 &&
        m.count <= MAX_UNREAD
      );
    case "message":
      return hasExactKeys(m, ["p", "type", "sender"]) && oneOf(m.sender, ["agent", "bot", "system"] as const);
    case "identity":
      return hasExactKeys(m, ["p", "type", "verified"]) && isBool(m.verified);
    default:
      return false;
  }
}

/** Strict check of a message the frame received from the host page (or admin preview). */
export function isHostToFrame(data: unknown): data is HostToFrame {
  const m = header(data);
  if (!m) return false;
  switch (m.type) {
    case "open":
    case "close":
      return hasExactKeys(m, ["p", "type"]);
    case "set-topic":
      return (
        hasExactKeys(m, ["p", "type", "topic"]) &&
        typeof m.topic === "string" &&
        TOPIC_KEY.test(m.topic)
      );
    case "preview-config":
      return hasExactKeys(m, ["p", "type", "config"]) && isPreviewConfig(m.config);
    case "logout":
      return hasExactKeys(m, ["p", "type"]);
    case "identify":
      return hasExactKeys(m, ["p", "type", "token"]) && isIdentityToken(m.token);
    default:
      return false;
  }
}
