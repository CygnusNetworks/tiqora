import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
} from "@simplewebauthn/browser";
import { api, ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

function browserSupportsWebAuthn(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential !== "undefined";
}

/**
 * Agent account security: TOTP 2FA enrollment with a backend-rendered SVG
 * QR code (GET /api/v1/auth/totp/enroll/qr, cookie-authenticated — plain
 * <img src> works because /api is same-origin, see vite.config.ts proxy),
 * plus WebAuthn passkey management when the backend has webauthn configured.
 */
export function SecurityPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [enrolling, setEnrolling] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [qrNonce, setQrNonce] = useState(0);
  const [confirmCode, setConfirmCode] = useState("");
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [disableCode, setDisableCode] = useState("");
  const [disableError, setDisableError] = useState<string | null>(null);
  const [passkeyError, setPasskeyError] = useState<string | null>(null);
  const [addingPasskey, setAddingPasskey] = useState(false);

  const webauthnSupported = useMemo(() => browserSupportsWebAuthn(), []);

  const methodsQuery = useQuery({
    queryKey: ["auth-methods"],
    queryFn: () => api.authMethods(),
  });
  const webauthnEnabled = Boolean(methodsQuery.data?.webauthn);

  const statusQuery = useQuery({
    queryKey: ["totp-status"],
    queryFn: () => api.totpStatus(),
  });

  const passkeysQuery = useQuery({
    queryKey: ["passkeys"],
    queryFn: () => api.passkeyList(),
    enabled: webauthnEnabled,
  });

  const enrollMutation = useMutation({
    mutationFn: () => api.totpEnroll(),
    onSuccess: (res) => {
      setSecret(res.secret);
      setQrNonce((n) => n + 1);
      setEnrolling(true);
      setConfirmError(null);
    },
  });

  const confirmMutation = useMutation({
    mutationFn: (code: string) => api.totpConfirm({ code }),
    onSuccess: () => {
      setEnrolling(false);
      setSecret(null);
      setConfirmCode("");
      setConfirmError(null);
      void queryClient.invalidateQueries({ queryKey: ["totp-status"] });
    },
    onError: () => {
      setConfirmError(t("security.confirmError"));
    },
  });

  const disableMutation = useMutation({
    mutationFn: (code: string) => api.totpDisable({ code }),
    onSuccess: () => {
      setDisableCode("");
      setDisableError(null);
      void queryClient.invalidateQueries({ queryKey: ["totp-status"] });
    },
    onError: () => {
      setDisableError(t("security.disableError"));
    },
  });

  const deletePasskeyMutation = useMutation({
    mutationFn: (id: number) => api.passkeyDelete(id),
    onSuccess: () => {
      setPasskeyError(null);
      void queryClient.invalidateQueries({ queryKey: ["passkeys"] });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 400) {
        setPasskeyError(t("security.passkeyDeleteLastError"));
      } else {
        setPasskeyError(t("security.passkeyDeleteError"));
      }
    },
  });

  const onConfirm = (e: FormEvent) => {
    e.preventDefault();
    setConfirmError(null);
    confirmMutation.mutate(confirmCode);
  };

  const onDisable = (e: FormEvent) => {
    e.preventDefault();
    setDisableError(null);
    disableMutation.mutate(disableCode);
  };

  const onAddPasskey = async () => {
    setPasskeyError(null);
    setAddingPasskey(true);
    try {
      const options = await api.passkeyRegisterBegin();
      const credential = await startRegistration({
        optionsJSON: options as unknown as PublicKeyCredentialCreationOptionsJSON,
      });
      const name =
        window.prompt(t("security.passkeyNamePrompt"), t("security.passkeyDefaultName")) ?? "";
      await api.passkeyRegisterFinish({
        credential: credential as unknown as Record<string, unknown>,
        name: name.trim() || null,
      });
      void queryClient.invalidateQueries({ queryKey: ["passkeys"] });
    } catch (err) {
      // User cancelled the browser ceremony — not an error to surface.
      if (err instanceof Error && /cancel|abort|notallowed/i.test(err.name + err.message)) {
        return;
      }
      setPasskeyError(t("security.passkeyAddError"));
    } finally {
      setAddingPasskey(false);
    }
  };

  const enabled = statusQuery.data?.enabled ?? false;
  const passkeys = passkeysQuery.data ?? [];

  return (
    <div className="mx-auto w-full max-w-lg px-4 py-8">
      <h1 className="font-display text-xl font-bold tracking-tight text-ink">
        {t("security.title")}
      </h1>

      <section className="mt-6 rounded-xl border border-hairline bg-surface p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink">{t("security.totpHeading")}</h2>
          {statusQuery.isLoading ? (
            <Spinner />
          ) : (
            <span data-testid="totp-status-badge">
              <Badge tone={enabled ? "success" : "muted"}>
                {enabled ? t("security.totpEnabled") : t("security.totpDisabled")}
              </Badge>
            </span>
          )}
        </div>

        {!enabled && !enrolling && (
          <Button
            className="mt-4"
            variant="primary"
            data-testid="totp-enroll-start"
            disabled={enrollMutation.isPending}
            onClick={() => enrollMutation.mutate()}
          >
            {enrollMutation.isPending ? <Spinner /> : t("security.enrollButton")}
          </Button>
        )}

        {enrolling && secret && (
          <div className="mt-4 space-y-4">
            <p className="text-sm text-muted">{t("security.scanHint")}</p>
            <img
              key={qrNonce}
              src="/api/v1/auth/totp/enroll/qr"
              alt="TOTP QR code"
              width={200}
              height={200}
              data-testid="totp-qr-image"
              className="rounded-lg border border-hairline bg-white p-2"
            />
            <p className="text-xs text-muted">
              {t("security.secretLabel")}{" "}
              <code data-testid="totp-secret" className="font-mono text-ink">
                {secret}
              </code>
            </p>
            <form onSubmit={onConfirm} className="space-y-3" data-testid="totp-confirm-form">
              <label className="block text-sm">
                <span className="mb-1 block text-muted">{t("security.confirmCode")}</span>
                <input
                  data-testid="totp-confirm-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  required
                  value={confirmCode}
                  onChange={(e) => setConfirmCode(e.target.value)}
                  className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
                />
              </label>
              {confirmError && (
                <p className="text-sm text-danger" role="alert" data-testid="totp-confirm-error">
                  {confirmError}
                </p>
              )}
              <Button
                type="submit"
                variant="primary"
                disabled={confirmMutation.isPending}
                data-testid="totp-confirm-submit"
              >
                {confirmMutation.isPending ? <Spinner /> : t("security.confirmButton")}
              </Button>
            </form>
          </div>
        )}

        {enabled && (
          <form onSubmit={onDisable} className="mt-4 space-y-3" data-testid="totp-disable-form">
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("security.disableCode")}</span>
              <input
                data-testid="totp-disable-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                value={disableCode}
                onChange={(e) => setDisableCode(e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
              />
            </label>
            {disableError && (
              <p className="text-sm text-danger" role="alert" data-testid="totp-disable-error">
                {disableError}
              </p>
            )}
            <Button
              type="submit"
              variant="danger"
              disabled={disableMutation.isPending}
              data-testid="totp-disable-submit"
            >
              {disableMutation.isPending ? <Spinner /> : t("security.disableConfirm")}
            </Button>
          </form>
        )}
      </section>

      {webauthnEnabled && (
        <section
          className="mt-6 rounded-xl border border-hairline bg-surface p-5"
          data-testid="passkeys-section"
        >
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">{t("security.passkeyHeading")}</h2>
            {passkeysQuery.isLoading ? (
              <Spinner />
            ) : (
              <span data-testid="passkey-status-badge">
                <Badge tone={passkeys.length > 0 ? "success" : "muted"}>
                  {passkeys.length > 0
                    ? t("security.passkeyCount", { count: passkeys.length })
                    : t("security.passkeyNone")}
                </Badge>
              </span>
            )}
          </div>

          <p className="mt-2 text-sm text-muted">{t("security.passkeyHint")}</p>

          {!webauthnSupported && (
            <p
              className="mt-3 text-sm text-danger"
              role="alert"
              data-testid="passkey-unsupported"
            >
              {t("security.passkeyUnsupported")}
            </p>
          )}

          {webauthnSupported && (
            <>
              {passkeys.length > 0 && (
                <ul className="mt-4 divide-y divide-hairline" data-testid="passkey-list">
                  {passkeys.map((pk) => (
                    <li
                      key={pk.id}
                      className="flex items-center justify-between gap-3 py-3"
                      data-testid={`passkey-item-${pk.id}`}
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-ink">{pk.name}</p>
                        <p className="text-xs text-muted">
                          {t("security.passkeyCreated", {
                            date: formatDateTime(pk.created, i18n.language),
                          })}
                          {pk.last_used_at
                            ? ` · ${t("security.passkeyLastUsed", {
                                date: formatDateTime(pk.last_used_at, i18n.language),
                              })}`
                            : ""}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="danger"
                        size="sm"
                        data-testid={`passkey-delete-${pk.id}`}
                        disabled={deletePasskeyMutation.isPending}
                        onClick={() => {
                          if (window.confirm(t("security.passkeyDeleteConfirm", { name: pk.name }))) {
                            deletePasskeyMutation.mutate(pk.id);
                          }
                        }}
                      >
                        {t("security.passkeyRemove")}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}

              {passkeyError && (
                <p className="mt-3 text-sm text-danger" role="alert" data-testid="passkey-error">
                  {passkeyError}
                </p>
              )}

              <Button
                className="mt-4"
                variant="primary"
                data-testid="passkey-add"
                disabled={addingPasskey}
                onClick={() => void onAddPasskey()}
              >
                {addingPasskey ? <Spinner /> : t("security.passkeyAdd")}
              </Button>
            </>
          )}
        </section>
      )}
    </div>
  );
}
