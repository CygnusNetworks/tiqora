import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
} from "@simplewebauthn/browser";
import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { OtpInput } from "@/components/ui/OtpInput";
import { Badge } from "@/components/ui/Badge";
import { useConfirm, usePrompt } from "@/components/ui/ConfirmDialog";
import { HelpPopover } from "@/components/ui/HelpPopover";
import type { TwoFactorApi } from "./twoFactorApi";

function browserSupportsWebAuthn(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential !== "undefined";
}

/**
 * The backend refuses passkey changes from a session that authenticated too
 * long ago ("sudo mode", see _require_recent_auth). It signals that with a 403
 * carrying the `reauthentication_required` detail, which reaches us as the
 * ApiError message — a plain 403 means something else and keeps its own text.
 */
function isReauthRequired(err: unknown): boolean {
  return (
    err instanceof ApiError && err.status === 403 && err.message === "reauthentication_required"
  );
}

/**
 * Second-factor management shared by the agent and the customer security
 * page: TOTP enrollment with a backend-rendered SVG QR code (cookie-
 * authenticated, same-origin so a plain <img src> works) plus WebAuthn passkey
 * management when the backend has WebAuthn configured. The backend differs
 * only by the `TwoFactorApi` it is given.
 */
export function TwoFactorPanel({
  api,
  testIdPrefix = "security",
}: {
  api: TwoFactorApi;
  testIdPrefix?: string;
}) {
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
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { prompt, dialog: promptDialog } = usePrompt();

  const webauthnSupported = useMemo(() => browserSupportsWebAuthn(), []);

  // Scoped by prefix so the agent and customer panels never share cache entries.
  const methodsKey = [testIdPrefix, "auth-methods"];
  const statusKey = [testIdPrefix, "totp-status"];
  const passkeysKey = [testIdPrefix, "passkeys"];

  const methodsQuery = useQuery({
    queryKey: methodsKey,
    queryFn: () => api.webauthnEnabled(),
  });
  const webauthnEnabled = Boolean(methodsQuery.data);

  const statusQuery = useQuery({
    queryKey: statusKey,
    queryFn: () => api.status(),
  });

  const passkeysQuery = useQuery({
    queryKey: passkeysKey,
    queryFn: () => api.listPasskeys(),
    enabled: webauthnEnabled,
  });

  const enrollMutation = useMutation({
    mutationFn: () => api.enroll(),
    onSuccess: (res) => {
      setSecret(res.secret);
      setQrNonce((n) => n + 1);
      setEnrolling(true);
      setConfirmError(null);
    },
  });

  const confirmMutation = useMutation({
    mutationFn: (code: string) => api.confirm(code),
    onSuccess: () => {
      setEnrolling(false);
      setSecret(null);
      setConfirmCode("");
      setConfirmError(null);
      void queryClient.invalidateQueries({ queryKey: statusKey });
    },
    onError: () => {
      setConfirmError(t("security.confirmError"));
    },
  });

  const disableMutation = useMutation({
    mutationFn: (code: string) => api.disable(code),
    onSuccess: () => {
      setDisableCode("");
      setDisableError(null);
      void queryClient.invalidateQueries({ queryKey: statusKey });
    },
    onError: () => {
      setDisableError(t("security.disableError"));
    },
  });

  const deletePasskeyMutation = useMutation({
    mutationFn: (id: number) => api.deletePasskey(id),
    onSuccess: () => {
      setPasskeyError(null);
      void queryClient.invalidateQueries({ queryKey: passkeysKey });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 400) {
        setPasskeyError(t("security.passkeyDeleteLastError"));
      } else if (isReauthRequired(err)) {
        setPasskeyError(t("security.passkeyReauthRequired"));
      } else {
        setPasskeyError(t("security.passkeyDeleteError"));
      }
    },
  });

  const onConfirm = (e: FormEvent) => {
    e.preventDefault();
    if (confirmMutation.isPending) return;
    setConfirmError(null);
    confirmMutation.mutate(confirmCode);
  };

  const onDisable = (e: FormEvent) => {
    e.preventDefault();
    if (disableMutation.isPending) return;
    setDisableError(null);
    disableMutation.mutate(disableCode);
  };

  const onAddPasskey = async () => {
    setPasskeyError(null);
    setAddingPasskey(true);
    try {
      const options = await api.registerBegin();
      const credential = await startRegistration({
        optionsJSON: options as unknown as PublicKeyCredentialCreationOptionsJSON,
      });
      const name =
        (await prompt({
          title: t("security.passkeyNamePrompt"),
          defaultValue: t("security.passkeyDefaultName"),
        })) ?? "";
      await api.registerFinish(credential, name);
      void queryClient.invalidateQueries({ queryKey: passkeysKey });
    } catch (err) {
      // User cancelled the browser ceremony — not an error to surface.
      if (err instanceof Error && /cancel|abort|notallowed/i.test(err.name + err.message)) {
        return;
      }
      setPasskeyError(
        isReauthRequired(err)
          ? t("security.passkeyReauthRequired")
          : t("security.passkeyAddError"),
      );
    } finally {
      setAddingPasskey(false);
    }
  };

  const enabled = statusQuery.data?.totp_enabled ?? false;
  const passkeys = passkeysQuery.data ?? [];

  return (
    <div>
      <section className="mt-6 rounded-xl border border-hairline bg-surface p-5">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5">
            <h2 className="text-sm font-semibold text-ink">{t("security.totpHeading")}</h2>
            <HelpPopover title={t("security.totpHeading")} testId={`${testIdPrefix}-help-totp`}>
              {t("security.help.totp")}
            </HelpPopover>
          </span>
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
              src={api.enrollQrUrl()}
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
              <div className="text-sm">
                <span className="mb-2 block text-muted">{t("security.confirmCode")}</span>
                <OtpInput
                  data-testid="totp-confirm-code"
                  aria-label={t("security.confirmCode")}
                  required
                  value={confirmCode}
                  onChange={(v) => {
                    setConfirmError(null);
                    setConfirmCode(v);
                  }}
                  onComplete={(code) => confirmMutation.mutate(code)}
                  status={confirmMutation.isPending ? "verifying" : confirmError ? "error" : "idle"}
                  statusMessage={confirmError}
                />
              </div>
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
            <div className="text-sm">
              <span className="mb-2 block text-muted">{t("security.disableCode")}</span>
              <OtpInput
                data-testid="totp-disable-code"
                aria-label={t("security.disableCode")}
                required
                value={disableCode}
                onChange={(v) => {
                  setDisableError(null);
                  setDisableCode(v);
                }}
                onComplete={(code) => disableMutation.mutate(code)}
                status={
                  disableMutation.isPending ? "verifying" : disableError ? "error" : "idle"
                }
                statusMessage={disableError}
              />
            </div>
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
            <span className="inline-flex items-center gap-1.5">
              <h2 className="text-sm font-semibold text-ink">{t("security.passkeyHeading")}</h2>
              <HelpPopover title={t("security.passkeyHeading")} testId={`${testIdPrefix}-help-passkey`}>
                {t("security.help.passkey")}
              </HelpPopover>
            </span>
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
                        onClick={async () => {
                          const ok = await confirm({
                            title: t("security.passkeyHeading"),
                            message: t("security.passkeyDeleteConfirm", { name: pk.name }),
                            variant: "danger",
                          });
                          if (ok) deletePasskeyMutation.mutate(pk.id);
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

      {confirmDialog}
      {promptDialog}
    </div>
  );
}
