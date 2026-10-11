import { HEX_COLOR, FIELD_KEY, TOPIC_KEY, withTopicKeys, type Values } from "./webchatForm";

/**
 * Copy of the widget's host -> frame protocol (`packages/webchat-widget/src/shared/protocol.ts`);
 * the app does not depend on the widget package.
 */
export const PROTOCOL = "tiqora-chat/1";
export type PreviewConfig = {
  theme: {
    primary_color: string;
    position: "right" | "left";
    mode: "auto" | "light" | "dark";
    launcher_label: string | null;
  };
  greeting: string | null;
  prechat_fields: {
    key: string;
    kind: "text" | "textarea" | "email" | "select" | "checkbox";
    label: string;
    required: boolean;
    options: { value: string; label: string }[];
  }[];
  topics: { key: string; label: string }[];
};

/**
 * The render-relevant subset of the values, or `null` while the editor holds an
 * intermediate state the frame would reject (R-PREVIEW-POST): the preview then keeps
 * the last valid configuration.
 */
export function toPreviewConfig(values: Values): PreviewConfig | null {
  const theme = values.theme;
  if (!HEX_COLOR.test(theme.primary_color) || (theme.launcher_label ?? "").length > 40) return null;
  if ((values.greeting ?? "").length > 500) return null;
  if (values.topics.length < 1 || values.topics.length > 20) return null;
  if (values.prechat_fields.length > 15) return null;
  const topics = withTopicKeys(values.topics.map((t) => ({ ...t, label: t.label.trim() })));
  if (topics.some((t) => !TOPIC_KEY.test(t.key) || t.label.length < 1 || t.label.length > 80)) return null;
  const fields = values.prechat_fields.map((f) => ({
    key: f.key,
    kind: f.kind,
    label: f.label.trim(),
    required: !!f.required,
    options: f.kind === "select" ? (f.options ?? []).map((o) => ({ value: o.value.trim(), label: o.label.trim() })) : [],
  }));
  for (const f of fields) {
    if (!FIELD_KEY.test(f.key) || f.label.length < 1 || f.label.length > 100) return null;
    if (f.kind === "select" && f.options.length === 0) return null;
    if (f.options.length > 50) return null;
    if (f.options.some((o) => !o.value || !o.label || o.value.length > 100 || o.label.length > 100)) return null;
  }
  const greeting = (values.greeting ?? "").trim();
  return {
    theme: {
      primary_color: theme.primary_color,
      position: theme.position ?? "right",
      mode: theme.mode ?? "auto",
      launcher_label: (theme.launcher_label ?? "").trim() || null,
    },
    greeting: greeting || null,
    prechat_fields: fields,
    topics: topics.map((t) => ({ key: t.key, label: t.label })),
  };
}

