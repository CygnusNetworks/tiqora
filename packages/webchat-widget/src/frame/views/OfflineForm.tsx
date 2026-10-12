import type { JSX } from "preact";
import type { ChatPublicConfig } from "../api";
import type { Translate } from "../i18n";
import { PrechatForm, type PrechatValues } from "./PrechatForm";

/** Offline form: the pre-chat form with a mandatory email (added when no field is one). */
export function OfflineForm(props: {
  config: ChatPublicConfig;
  t: Translate;
  topic: string | null;
  onTopic: (key: string) => void;
  busy: boolean;
  serverErrors: Record<string, string>;
  formError: string | null;
  onSubmit: (v: PrechatValues) => void;
}): JSX.Element {
  const { config, t } = props;
  const hasEmail = config.prechat_fields.some((f) => f.kind === "email");
  const fields = hasEmail
    ? config.prechat_fields.map((f) => (f.kind === "email" ? { ...f, required: true } : f))
    : [
        ...config.prechat_fields,
        { key: "email", kind: "email" as const, label: t("contact.title"), required: true, options: [], identity_supplied: false },
      ];
  return (
    <PrechatForm
      {...props}
      fields={fields}
      title={t("offline.title")}
      intro={t("offline.text")}
      submitLabel={t("offline.send")}
    />
  );
}
