import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { portalApi, ApiError, type PortalPreferencesOut } from "@/lib/portalApi";
import { useCustomerAuth } from "@/auth/CustomerAuthContext";
import { localePickerItems, resolveLocaleCode, setAppLanguage } from "@/i18n";
import { CryptoKeysPanel } from "@/components/crypto/CryptoKeysPanel";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from "@/lib/passwordPolicy";

const QUERY_KEY = ["portal", "preferences"] as const;

const INPUT_CLASS =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";

function errText(err: unknown): string {
  return err instanceof ApiError || err instanceof Error ? err.message : String(err);
}

/**
 * Customer preferences (Znuny CustomerPreferences): interface language,
 * password change and — while PGP / S/MIME are enabled — the customer's own
 * PGP key / S/MIME certificate. Groups switched off in Znuny's SysConfig are
 * not shown.
 */
export function PreferencesPage() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const { customer } = useCustomerAuth();
  const q = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => portalApi.portalPreferences(signal),
  });

  if (q.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (q.isError || !q.data) {
    return <p className="text-sm text-danger">{errText(q.error)}</p>;
  }
  const prefs = q.data;
  const store = (next: PortalPreferencesOut) => qc.setQueryData(QUERY_KEY, next);

  return (
    <div className="space-y-6" data-testid="portal-preferences-page">
      <h1 className="font-display text-xl font-semibold text-ink">
        {t("portal.preferences.title")}
      </h1>

      {prefs.language_enabled && (
        <LanguageSection
          current={resolveLocaleCode(prefs.language ?? i18n.language)}
          onSave={async (code) => {
            store(await portalApi.portalSetLanguage(code));
            await setAppLanguage(code, { persistRemote: false });
          }}
        />
      )}

      {prefs.password_enabled && <PasswordSection />}

      {(prefs.pgp_enabled || prefs.smime_enabled) && (
        <section
          className="space-y-3 rounded-lg border border-hairline bg-surface p-4"
          data-testid="portal-preferences-keys"
        >
          <div>
            <h2 className="text-sm font-semibold text-ink">{t("portal.preferences.keys")}</h2>
            <p className="text-xs text-muted">
              {t("portal.preferences.keysDesc", { email: customer?.email ?? "" })}
            </p>
          </div>
          <CryptoKeysPanel
            data={prefs.keys}
            testId="portal-keys"
            handlers={{
              uploadPgp: async (armor) => store(await portalApi.portalUploadPgpKey(armor)),
              uploadSmime: async (cert) =>
                store(await portalApi.portalUploadSmimeCertificate(cert)),
            }}
          />
        </section>
      )}
    </div>
  );
}

function LanguageSection({
  current,
  onSave,
}: {
  current: string;
  onSave: (code: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(current);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
      <div>
        <h2 className="text-sm font-semibold text-ink">{t("portal.preferences.language")}</h2>
        <p className="text-xs text-muted">{t("portal.preferences.languageDesc")}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="w-64">
          <SelectField
            items={localePickerItems()}
            value={value}
            onChange={setValue}
            testId="portal-preferences-language"
            aria-label={t("portal.preferences.language")}
          />
        </div>
        <Button
          variant="primary"
          size="sm"
          disabled={busy}
          data-testid="portal-preferences-language-save"
          onClick={() => {
            setBusy(true);
            setNotice(null);
            void onSave(value)
              .then(() => setNotice({ ok: true, text: t("portal.preferences.languageSaved") }))
              .catch((err: unknown) => setNotice({ ok: false, text: errText(err) }))
              .finally(() => setBusy(false));
          }}
        >
          {t("portal.preferences.save")}
        </Button>
      </div>
      {notice && (
        <p className={notice.ok ? "text-sm text-green" : "text-sm text-danger"} role="status">
          {notice.text}
        </p>
      )}
    </section>
  );
}

function PasswordSection() {
  const { t } = useTranslation();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setNotice(null);
    if (next !== repeat) {
      setNotice({ ok: false, text: t("portal.preferences.passwordMismatch") });
      return;
    }
    setBusy(true);
    try {
      await portalApi.portalChangePassword(current, next);
      setCurrent("");
      setNext("");
      setRepeat("");
      setNotice({ ok: true, text: t("portal.preferences.passwordSaved") });
    } catch (err) {
      setNotice({ ok: false, text: errText(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      onSubmit={(e) => void onSubmit(e)}
      className="space-y-3 rounded-lg border border-hairline bg-surface p-4"
      data-testid="portal-preferences-password"
    >
      <div>
        <h2 className="text-sm font-semibold text-ink">{t("portal.preferences.password")}</h2>
        <p className="text-xs text-muted">{t("portal.preferences.passwordDesc")}</p>
      </div>
      {(
        [
          ["current", current, setCurrent, "portal.preferences.currentPassword", "current-password"],
          ["new", next, setNext, "portal.preferences.newPassword", "new-password"],
          ["repeat", repeat, setRepeat, "portal.preferences.repeatPassword", "new-password"],
        ] as const
      ).map(([id, value, set, label, autoComplete]) => (
        <label key={id} className="block text-sm">
          <span className="mb-1 block text-muted">{t(label)}</span>
          <input
            type="password"
            required
            autoComplete={autoComplete}
            maxLength={id === "current" ? undefined : MAX_PASSWORD_LENGTH}
            minLength={id === "current" ? undefined : MIN_PASSWORD_LENGTH}
            value={value}
            onChange={(e) => set(e.target.value)}
            data-testid={`portal-preferences-password-${id}`}
            className={INPUT_CLASS}
          />
        </label>
      ))}
      <Button
        type="submit"
        variant="primary"
        size="sm"
        disabled={busy}
        data-testid="portal-preferences-password-save"
      >
        {t("portal.preferences.save")}
      </Button>
      {notice && (
        <p
          className={notice.ok ? "text-sm text-green" : "text-sm text-danger"}
          role={notice.ok ? "status" : "alert"}
          data-testid="portal-preferences-password-notice"
        >
          {notice.text}
        </p>
      )}
    </form>
  );
}
