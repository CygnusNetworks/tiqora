import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { logoUrl } from "@/lib/assets";
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from "@/lib/passwordPolicy";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";


/**
 * Public landing page for the one-time link a new agent receives by mail.
 *
 * The token is checked before the form is shown, so an expired or already
 * spent link says so rather than letting someone type a password into a
 * dead form. On success it sends the agent to the login page — deliberately
 * not an auto-login, so the new password gets used once straight away.
 */
export function SetPasswordPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { token } = useSearch({ from: "/set-password" }) as { token?: string };

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  const checkQ = useQuery({
    queryKey: ["password-setup", token],
    queryFn: ({ signal }) => api.checkPasswordSetup(token!, signal),
    enabled: Boolean(token),
    retry: false,
  });

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirm.length > 0 && password !== confirm;
  const canSubmit =
    password.length >= MIN_PASSWORD_LENGTH &&
    password.length <= MAX_PASSWORD_LENGTH &&
    password === confirm &&
    !submitting;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit || !token) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.completePasswordSetup(token, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const shell = (children: React.ReactNode) => (
    <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm rounded-xl border border-hairline bg-surface p-8">
        <img src={logoUrl} alt="" width={36} height={36} className="mx-auto mb-3" />
        <h1 className="text-center font-display text-2xl font-bold tracking-tight text-ink">
          {t("app.name")}
        </h1>
        {children}
      </div>
    </div>
  );

  if (!token || (!checkQ.isLoading && checkQ.data?.valid !== true)) {
    return shell(
      <div className="mt-4 space-y-3" data-testid="set-password-invalid">
        <p className="text-center text-sm text-danger">{t("setPassword.invalid")}</p>
        <p className="text-center text-xs text-muted">{t("setPassword.invalidHint")}</p>
        <Button
          variant="secondary"
          className="w-full"
          onClick={() => void navigate({ to: "/login" })}
        >
          {t("setPassword.toLogin")}
        </Button>
      </div>,
    );
  }

  if (checkQ.isLoading) {
    return shell(
      <div className="mt-6 flex justify-center">
        <Spinner />
      </div>,
    );
  }

  if (done) {
    return shell(
      <div className="mt-4 space-y-3" data-testid="set-password-done">
        <p className="text-center text-sm text-green">{t("setPassword.done")}</p>
        <Button
          variant="primary"
          className="w-full"
          data-testid="set-password-to-login"
          onClick={() => void navigate({ to: "/login" })}
        >
          {t("setPassword.toLogin")}
        </Button>
      </div>,
    );
  }

  return shell(
    <>
      <p className="mt-1.5 text-center text-sm text-muted">{t("setPassword.subtitle")}</p>
      <p className="mt-1 text-center text-sm font-medium text-ink">{checkQ.data?.login}</p>
      <form className="mt-5 space-y-3" onSubmit={onSubmit} data-testid="set-password-form">
        <div className="space-y-1">
          <label htmlFor="new-password" className="text-xs font-medium text-muted">
            {t("setPassword.password")}
          </label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            autoFocus
            maxLength={MAX_PASSWORD_LENGTH}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            data-testid="set-password-input"
            className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
          />
          <p className="text-xs text-muted">
            {t("setPassword.lengthHint", {
              min: MIN_PASSWORD_LENGTH,
              max: MAX_PASSWORD_LENGTH,
            })}
          </p>
        </div>
        <div className="space-y-1">
          <label htmlFor="confirm-password" className="text-xs font-medium text-muted">
            {t("setPassword.confirm")}
          </label>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            maxLength={MAX_PASSWORD_LENGTH}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            data-testid="set-password-confirm"
            className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
          />
          {mismatch && (
            <p className="text-xs text-danger" data-testid="set-password-mismatch">
              {t("setPassword.mismatch")}
            </p>
          )}
          {tooShort && !mismatch && (
            <p className="text-xs text-danger">
              {t("setPassword.lengthHint", {
                min: MIN_PASSWORD_LENGTH,
                max: MAX_PASSWORD_LENGTH,
              })}
            </p>
          )}
        </div>
        {error && (
          <p className="text-sm text-danger" role="alert" data-testid="set-password-error">
            {error}
          </p>
        )}
        <Button
          type="submit"
          variant="primary"
          className="w-full"
          disabled={!canSubmit}
          data-testid="set-password-submit"
        >
          {submitting ? t("setPassword.saving") : t("setPassword.submit")}
        </Button>
      </form>
    </>,
  );
}
