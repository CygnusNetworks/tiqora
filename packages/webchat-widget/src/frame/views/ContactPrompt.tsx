import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { Translate } from "../i18n";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Asks for an email address after a chat ended without an agent. */
export function ContactPrompt(props: {
  t: Translate;
  onSave: (email: string) => Promise<string | null>;
}): JSX.Element {
  const { t } = props;
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  if (done) return <p role="status" class="tq-sys">{t("contact.saved")}</p>;
  const submit = async (ev: Event) => {
    ev.preventDefault();
    const v = email.trim();
    if (!EMAIL.test(v)) {
      setError(t("prechat.invalidEmail"));
      return;
    }
    setBusy(true);
    const err = await props.onSave(v);
    setBusy(false);
    if (err) setError(err);
    else setDone(true);
  };
  return (
    <form class="tq-form tq-contact" onSubmit={submit} noValidate>
      <label for="tq-contact-email">{t("contact.title")}</label>
      <input
        id="tq-contact-email"
        type="email"
        autocomplete="email"
        value={email}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={error ? "tq-contact-err" : undefined}
        onInput={(e) => {
          setEmail(e.currentTarget.value);
          setError(null);
        }}
      />
      {error && (
        <p class="tq-err" id="tq-contact-err">
          {error}
        </p>
      )}
      <button class="tq-btn" type="submit" disabled={busy}>
        {t("contact.save")}
      </button>
    </form>
  );
}
