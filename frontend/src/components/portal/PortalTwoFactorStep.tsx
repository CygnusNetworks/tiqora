import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useCustomerAuth } from "@/auth/CustomerAuthContext";
import { ApiError } from "@/lib/portalApi";
import { Button } from "@/components/ui/Button";
import { OtpInput } from "@/components/ui/OtpInput";
import { Spinner } from "@/components/ui/Spinner";

function browserSupportsWebAuthn(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential !== "undefined";
}

/**
 * Second step of a customer sign-in after the password was accepted: the
 * authenticator code and/or a passkey, depending on what the customer has set
 * up. Calls `onDone` once the portal session is complete.
 */
export function PortalTwoFactorStep({
  factors,
  onDone,
}: {
  factors: { totp: boolean; passkey: boolean };
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const { verifyTotp, verifyPasskey } = useCustomerAuth();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const runTotp = async (value: string) => {
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await verifyTotp(value);
      onDone();
    } catch (err) {
      setError(
        err instanceof ApiError && (err.status === 401 || err.status === 400)
          ? t("portal.login.totpInvalid")
          : t("auth.loginFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const onTotpSubmit = (e: FormEvent) => {
    e.preventDefault();
    void runTotp(code);
  };

  const onPasskey = async () => {
    setError(null);
    setSubmitting(true);
    try {
      await verifyPasskey();
      onDone();
    } catch (err) {
      if (err instanceof Error && /cancel|abort|notallowed/i.test(err.name + err.message)) {
        return;
      }
      setError(
        err instanceof ApiError && (err.status === 401 || err.status === 400)
          ? t("portal.login.passkeyInvalid")
          : t("portal.login.passkeyFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div data-testid="portal-2fa-step">
      <h1 className="text-center font-display text-2xl font-bold tracking-tight text-ink">
        {t("portal.login.twoFactorTitle")}
      </h1>
      <p className="mt-1.5 text-center text-sm text-muted">
        {factors.totp ? t("portal.login.twoFactorHint") : t("portal.login.passkeyOnlyHint")}
      </p>

      {factors.totp && (
        <form onSubmit={onTotpSubmit} className="mt-7 space-y-4" data-testid="portal-2fa-form">
          <div className="text-sm">
            <span className="mb-2 block text-muted">{t("portal.login.totpLabel")}</span>
            <OtpInput
              data-testid="portal-2fa-code"
              name="code"
              aria-label={t("portal.login.totpLabel")}
              required
              autoFocus
              value={code}
              onChange={(v) => {
                setError(null);
                setCode(v);
              }}
              onComplete={(v) => void runTotp(v)}
              status={submitting ? "verifying" : error ? "error" : "idle"}
              statusMessage={error}
            />
          </div>
          <Button
            type="submit"
            variant="primary"
            className="w-full"
            disabled={submitting}
            data-testid="portal-2fa-submit"
          >
            {submitting ? <Spinner /> : t("portal.login.verify")}
          </Button>
        </form>
      )}

      {factors.passkey && (
        <>
          {factors.totp && (
            <div className="my-4 flex items-center gap-3 text-xs text-muted">
              <span className="h-px flex-1 bg-hairline" />
              {t("portal.login.ssoDivider")}
              <span className="h-px flex-1 bg-hairline" />
            </div>
          )}
          {!browserSupportsWebAuthn() && (
            <p className="mt-4 text-sm text-danger" role="alert" data-testid="portal-2fa-unsupported">
              {t("portal.login.passkeyUnsupported")}
            </p>
          )}
          {error && !factors.totp && (
            <p className="mt-4 text-sm text-danger" role="alert" data-testid="portal-2fa-error">
              {error}
            </p>
          )}
          <Button
            type="button"
            variant={factors.totp ? "secondary" : "primary"}
            className={factors.totp ? "w-full" : "mt-6 w-full"}
            disabled={submitting}
            data-testid="portal-2fa-passkey"
            onClick={() => void onPasskey()}
          >
            {submitting ? <Spinner /> : t("portal.login.passkeyButton")}
          </Button>
        </>
      )}
    </div>
  );
}
