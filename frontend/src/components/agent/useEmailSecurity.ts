import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  type CryptoComposeBackendOut,
  type CryptoOptionsOut,
  type EmailSecurityIn,
} from "@/lib/api";

export type SecurityMode = "none" | "sign" | "encrypt" | "sign_encrypt";
type Backend = EmailSecurityIn["backend"];
type Method = NonNullable<EmailSecurityIn["method"]>;

export type EmailSecurityState = {
  mode: SecurityMode;
  backend: Backend;
  method: Method;
  signKey: string;
};

export const signs = (m: SecurityMode) => m === "sign" || m === "sign_encrypt";
export const encrypts = (m: SecurityMode) => m === "encrypt" || m === "sign_encrypt";

function initialState(options: CryptoOptionsOut): EmailSecurityState {
  const d = options.default;
  if (d) {
    return {
      mode: "sign",
      backend: d.backend,
      method: d.method ?? "detached",
      signKey: d.sign_key ?? "",
    };
  }
  const first = (options.backends ?? []).find((b) => b.available);
  return {
    mode: "none",
    backend: first?.backend ?? "pgp",
    method: "detached",
    signKey: "",
  };
}

/**
 * State + validation of the compose "Sicherheit" control (PGP / S/MIME).
 *
 * Loads `GET /tickets/{id}/crypto-options` (or the queue variant for a new
 * ticket), preselects the queue's default sign key (else no security) and
 * derives `payload` (the `email_security` request field) and `blocked` —
 * a reason why sending must not happen (encryption with a recipient that
 * has no usable key, signing without a key). With both backends disabled
 * the options say `enabled: false`: no control, `payload` null.
 */
export function useEmailSecurity({
  ticketId,
  queueId,
  to,
  cc,
  bcc,
  enabled = true,
}: {
  ticketId: number | null;
  queueId?: number | null;
  to: string | null;
  cc: string | null;
  bcc: string | null;
  enabled?: boolean;
}) {
  const { t } = useTranslation();
  const scope = ticketId ?? (queueId ? `q${queueId}` : null);
  // Recipients are typed character by character — ask the server once the
  // typing settles.
  const recipientsKey = [to ?? "", cc ?? "", bcc ?? ""].join("\u0000");
  const [settled, setSettled] = useState(recipientsKey);
  useEffect(() => {
    const id = setTimeout(() => setSettled(recipientsKey), 400);
    return () => clearTimeout(id);
  }, [recipientsKey]);
  const [qTo, qCc, qBcc] = settled.split("\u0000");
  const optionsQ = useQuery({
    queryKey: ["crypto-options", scope, qTo, qCc, qBcc],
    queryFn: ({ signal }) =>
      api.getCryptoOptions(
        ticketId,
        {
          queue_id: ticketId == null ? (queueId ?? undefined) : undefined,
          to: qTo || undefined,
          cc: qCc || undefined,
          bcc: qBcc || undefined,
        },
        signal,
      ),
    enabled: enabled && scope != null,
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
  const options = optionsQ.data ?? null;

  const [state, setState] = useState<EmailSecurityState | null>(null);
  // Seed once per ticket/queue (the queue default sign key); later option
  // refreshes (recipients typed) keep what the agent chose.
  const [seededFor, setSeededFor] = useState<string | number | null>(null);
  useEffect(() => {
    if (!options || !options.enabled || seededFor === scope) return;
    setState(initialState(options));
    setSeededFor(scope);
  }, [options, scope, seededFor]);

  const active = options?.enabled && state ? state : null;
  const backendOpts: CryptoComposeBackendOut | null =
    (active && (options?.backends ?? []).find((b) => b.backend === active.backend)) ||
    null;

  const blocked = useMemo(() => {
    if (!active || active.mode === "none") return null;
    if (!backendOpts || !backendOpts.available) {
      return t("ticket.emailSecurity.unavailable");
    }
    if (signs(active.mode) && !active.signKey) {
      return t("ticket.emailSecurity.noSignKey");
    }
    if (encrypts(active.mode)) {
      if (settled !== recipientsKey || optionsQ.isFetching) {
        return t("ticket.emailSecurity.checking");
      }
      const missing = (backendOpts.recipients ?? [])
        .filter((r) => r.status !== "ok")
        .map((r) => r.address);
      if (missing.length > 0) {
        return t("ticket.emailSecurity.missingKeys", { addresses: missing.join(", ") });
      }
      if ((backendOpts.recipients ?? []).length === 0) {
        return t("ticket.emailSecurity.noRecipients");
      }
    }
    return null;
  }, [active, backendOpts, t, settled, recipientsKey, optionsQ.isFetching]);

  const payload: EmailSecurityIn | null =
    active && active.mode !== "none"
      ? {
          backend: active.backend,
          method: active.backend === "pgp" ? active.method : "detached",
          sign_key: signs(active.mode) ? active.signKey || null : null,
          encrypt: encrypts(active.mode),
        }
      : null;

  return {
    options,
    state: active,
    setState,
    backendOpts,
    blocked,
    payload,
    loading: optionsQ.isLoading,
  };
}

export type EmailSecurity = ReturnType<typeof useEmailSecurity>;
