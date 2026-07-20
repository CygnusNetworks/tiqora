import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  type MailOutboundOut,
  type MailOutboundUpdate,
  type MailSecurity,
  type MailAuthType,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

const QUERY_KEY = ["admin", "mail", "outbound"] as const;

type FormState = {
  enabled: boolean;
  host: string;
  port: number;
  security: MailSecurity;
  auth_type: MailAuthType;
  auth_user: string;
  auth_password: string;
  from_default: string;
  timeout_seconds: number;
};

function toForm(row: MailOutboundOut): FormState {
  return {
    enabled: row.enabled,
    host: row.host,
    port: row.port,
    security: row.security,
    auth_type: row.auth_type,
    auth_user: row.auth_user,
    auth_password: "",
    from_default: row.from_default,
    timeout_seconds: row.timeout_seconds,
  };
}

const emptyForm: FormState = {
  enabled: false,
  host: "",
  port: 25,
  security: "none",
  auth_type: "none",
  auth_user: "",
  auth_password: "",
  from_default: "",
  timeout_seconds: 60,
};

export function MailOutboundPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [form, setForm] = useState<FormState>(emptyForm);
  const [testTo, setTestTo] = useState("");
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const settingsQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => api.getMailOutbound(signal),
  });

  useEffect(() => {
    if (settingsQ.data) {
      setForm(toForm(settingsQ.data));
    }
  }, [settingsQ.data]);

  const saveM = useMutation({
    mutationFn: (body: MailOutboundUpdate) => api.putMailOutbound(body),
    onSuccess: (data) => {
      qc.setQueryData(QUERY_KEY, data);
      setForm(toForm(data));
      setStatusMsg(t("admin.mailOutbound.saved"));
    },
    onError: () => setStatusMsg(t("admin.mailOutbound.saveError")),
  });

  const testM = useMutation({
    mutationFn: () =>
      api.testMailOutbound(testTo.trim() ? { to_address: testTo.trim() } : {}),
    onSuccess: (res) => {
      setTestMsg({
        ok: res.ok,
        text: res.ok
          ? `${t("admin.mailOutbound.testSuccess")}: ${res.message}${res.detail ? ` — ${res.detail}` : ""}`
          : `${t("admin.mailOutbound.testFailure")}: ${res.message}${res.detail ? ` — ${res.detail}` : ""}`,
      });
    },
    onError: (err) => {
      setTestMsg({
        ok: false,
        text: `${t("admin.mailOutbound.testFailure")}: ${String(err)}`,
      });
    },
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    const body: MailOutboundUpdate = {
      enabled: form.enabled,
      host: form.host,
      port: form.port,
      security: form.security,
      auth_type: form.auth_type,
      auth_user: form.auth_user,
      from_default: form.from_default,
      timeout_seconds: form.timeout_seconds,
    };
    if (form.auth_password.trim()) {
      body.auth_password = form.auth_password;
    }
    saveM.mutate(body);
  };

  const hasPassword = settingsQ.data?.has_password ?? false;

  if (settingsQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-mail-outbound-page">
        <Spinner />
      </div>
    );
  }

  if (settingsQ.isError) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-mail-outbound-page">
        {t("admin.mailOutbound.loadError")}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4" data-testid="admin-mail-outbound-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.mailOutbound.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.mailOutbound.description")}</p>
      </div>

      <form onSubmit={onSubmit} className="space-y-4 rounded-lg border border-hairline bg-surface p-4">
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            data-testid="mail-outbound-enabled"
            checked={form.enabled}
            onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            className="rounded border-hairline"
          />
          {t("admin.mailOutbound.enabled")}
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm sm:col-span-2">
            <span className="mb-1 block text-muted">{t("admin.mailOutbound.host")}</span>
            <input
              data-testid="mail-outbound-host"
              type="text"
              value={form.host}
              onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              autoComplete="off"
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.mailOutbound.port")}</span>
            <input
              data-testid="mail-outbound-port"
              type="number"
              min={1}
              max={65535}
              value={form.port}
              onChange={(e) => setForm((f) => ({ ...f, port: Number(e.target.value) || 25 }))}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.mailOutbound.security")}</span>
            <select
              data-testid="mail-outbound-security"
              value={form.security}
              onChange={(e) =>
                setForm((f) => ({ ...f, security: e.target.value as MailSecurity }))
              }
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            >
              <option value="none">{t("admin.mailOutbound.securityNone")}</option>
              <option value="starttls">{t("admin.mailOutbound.securityStarttls")}</option>
              <option value="ssl">{t("admin.mailOutbound.securitySsl")}</option>
            </select>
          </label>

          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.mailOutbound.authType")}</span>
            <select
              data-testid="mail-outbound-auth-type"
              value={form.auth_type}
              onChange={(e) =>
                setForm((f) => ({ ...f, auth_type: e.target.value as MailAuthType }))
              }
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            >
              <option value="none">{t("admin.mailOutbound.authNone")}</option>
              <option value="password">{t("admin.mailOutbound.authPassword")}</option>
            </select>
          </label>

          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.mailOutbound.authUser")}</span>
            <input
              data-testid="mail-outbound-auth-user"
              type="text"
              value={form.auth_user}
              onChange={(e) => setForm((f) => ({ ...f, auth_user: e.target.value }))}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              autoComplete="username"
            />
          </label>

          <label className="block text-sm sm:col-span-2">
            <span className="mb-1 flex items-center gap-2 text-muted">
              {t("admin.mailOutbound.authPasswordField")}
              {hasPassword ? (
                <span
                  data-testid="mail-outbound-password-set"
                  className="rounded bg-surface-subtle px-1.5 py-0.5 text-[11px] font-medium text-accent"
                >
                  {t("admin.mailOutbound.passwordSet")}
                </span>
              ) : null}
            </span>
            <input
              data-testid="mail-outbound-auth-password"
              type="password"
              value={form.auth_password}
              onChange={(e) => setForm((f) => ({ ...f, auth_password: e.target.value }))}
              placeholder={t("admin.mailOutbound.passwordPlaceholder")}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              autoComplete="new-password"
            />
          </label>

          <label className="block text-sm sm:col-span-2">
            <span className="mb-1 block text-muted">{t("admin.mailOutbound.fromDefault")}</span>
            <input
              data-testid="mail-outbound-from-default"
              type="text"
              value={form.from_default}
              onChange={(e) => setForm((f) => ({ ...f, from_default: e.target.value }))}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              autoComplete="off"
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.mailOutbound.timeoutSeconds")}</span>
            <input
              data-testid="mail-outbound-timeout"
              type="number"
              min={1}
              max={600}
              value={form.timeout_seconds}
              onChange={(e) =>
                setForm((f) => ({ ...f, timeout_seconds: Number(e.target.value) || 60 }))
              }
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            />
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={saveM.isPending} data-testid="mail-outbound-save">
            {saveM.isPending ? t("admin.mailOutbound.saving") : t("admin.mailOutbound.save")}
          </Button>
          {statusMsg ? (
            <span className="text-sm text-muted" data-testid="mail-outbound-status">
              {statusMsg}
            </span>
          ) : null}
        </div>
      </form>

      <div className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("admin.mailOutbound.testTo")}</span>
          <input
            data-testid="mail-outbound-test-to"
            type="email"
            value={testTo}
            onChange={(e) => setTestTo(e.target.value)}
            className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
            autoComplete="off"
          />
        </label>
        <Button
          type="button"
          variant="secondary"
          disabled={testM.isPending}
          onClick={() => {
            setTestMsg(null);
            testM.mutate();
          }}
          data-testid="mail-outbound-test"
        >
          {testM.isPending ? t("admin.mailOutbound.testing") : t("admin.mailOutbound.test")}
        </Button>
        {testMsg ? (
          <p
            data-testid="mail-outbound-test-result"
            className={`text-sm ${testMsg.ok ? "text-accent" : "text-danger"}`}
          >
            {testMsg.text}
          </p>
        ) : null}
      </div>
    </div>
  );
}
