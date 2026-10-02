import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type ChannelConfigOut, type ChannelConfigUpdate } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { SelectField } from "@/components/ui/SelectField";

const QUERY_KEY = ["admin", "channels", "telegram"] as const;
const CHANNEL = "telegram";

type TelegramMode = "polling" | "webhook";

/** Bot texts that fall back to a built-in English/German default (picked by
 * the customer's language, backend channels/telegram/texts.py) when empty. */
const TEXT_FIELDS = [
  { key: "start_text", label: "admin.telegram.startText", help: "admin.telegram.startTextHelp", rows: 2 },
  { key: "tone_prompt", label: "admin.telegram.tonePrompt", help: "admin.telegram.tonePromptHelp", rows: 3 },
  { key: "consent_button_label", label: "admin.telegram.consentButtonLabel", help: null, rows: 1 },
  {
    key: "identity_no_match_text",
    label: "admin.telegram.identityNoMatchText",
    help: "admin.telegram.identityNoMatchTextHelp",
    rows: 2,
  },
  { key: "identity_handoff_text", label: "admin.telegram.identityHandoffText", help: null, rows: 2 },
] as const;
type TextKey = (typeof TEXT_FIELDS)[number]["key"];
const emptyTexts = (): Record<TextKey, string> => ({
  start_text: "",
  tone_prompt: "",
  consent_button_label: "",
  identity_no_match_text: "",
  identity_handoff_text: "",
});

type FormState = {
  enabled: boolean;
  mode: TelegramMode;
  bot_token: string;
  queue_name: string;
  default_customer_user: string;
  webhook_url: string;
  webhook_secret_token: string;
  consent_required: boolean;
  consent_text: string;
  consent_confirmed_text: string;
  followup_reopen_days: string;
  texts: Record<TextKey, string>;
};

const emptyForm: FormState = {
  enabled: false,
  mode: "polling",
  bot_token: "",
  queue_name: "",
  default_customer_user: "",
  webhook_url: "",
  webhook_secret_token: "",
  consent_required: true,
  consent_text: "",
  consent_confirmed_text: "",
  followup_reopen_days: "",
  texts: emptyTexts(),
};

function toForm(row: ChannelConfigOut): FormState {
  const c = row.config;
  return {
    enabled: row.enabled,
    mode: c.mode === "webhook" ? "webhook" : "polling",
    bot_token: c.bot_token ?? "",
    queue_name: c.queue_name ?? "",
    default_customer_user: c.default_customer_user ?? "",
    webhook_url: c.webhook_url ?? "",
    webhook_secret_token: c.webhook_secret_token ?? "",
    consent_required: (c.consent_required ?? "1") !== "0",
    consent_text: c.consent_text ?? "",
    consent_confirmed_text: c.consent_confirmed_text ?? "",
    followup_reopen_days: c.followup_reopen_days ?? "",
    texts: Object.fromEntries(TEXT_FIELDS.map((f) => [f.key, c[f.key] ?? ""])) as Record<
      TextKey,
      string
    >,
  };
}

export function TelegramChannelPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [form, setForm] = useState<FormState>(emptyForm);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [webhookMsg, setWebhookMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const configQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => api.adminChannels.get(CHANNEL, signal),
  });

  const queuesQ = useQuery({
    queryKey: ["admin", "queues", "picker"],
    queryFn: ({ signal }) => api.adminQueues.list({ page: 1, pageSize: 200, valid: "valid" }, signal),
    staleTime: 60_000,
  });
  const queueOptions = (queuesQ.data?.items ?? []).map((q) => ({ value: q.name, label: q.name }));

  useEffect(() => {
    if (configQ.data) {
      setForm(toForm(configQ.data));
    }
  }, [configQ.data]);

  const saveM = useMutation({
    mutationFn: (body: ChannelConfigUpdate) => api.adminChannels.update(CHANNEL, body),
    onSuccess: (data) => {
      qc.setQueryData(QUERY_KEY, data);
      setForm(toForm(data));
      setStatusMsg(t("admin.telegram.saved"));
    },
    onError: () => setStatusMsg(t("admin.telegram.saveError")),
  });

  const registerM = useMutation({
    mutationFn: () =>
      api.telegramWebhookRegister(form.webhook_url.trim() ? { url: form.webhook_url.trim() } : {}),
    onSuccess: (res) => {
      setWebhookMsg({
        ok: res.ok,
        text: res.ok
          ? `${t("admin.telegram.webhookRegisterSuccess")}: ${res.url}`
          : t("admin.telegram.webhookRegisterFailure"),
      });
    },
    onError: (err) => {
      setWebhookMsg({ ok: false, text: `${t("admin.telegram.webhookRegisterFailure")}: ${String(err)}` });
    },
  });

  const unregisterM = useMutation({
    mutationFn: () => api.telegramWebhookUnregister(),
    onSuccess: (res) => {
      setWebhookMsg({
        ok: res.ok,
        text: res.ok
          ? t("admin.telegram.webhookUnregisterSuccess")
          : t("admin.telegram.webhookUnregisterFailure"),
      });
    },
    onError: (err) => {
      setWebhookMsg({
        ok: false,
        text: `${t("admin.telegram.webhookUnregisterFailure")}: ${String(err)}`,
      });
    },
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    const body: ChannelConfigUpdate = {
      enabled: form.enabled,
      config: {
        mode: form.mode,
        bot_token: form.bot_token,
        queue_name: form.queue_name,
        default_customer_user: form.default_customer_user,
        webhook_url: form.webhook_url,
        webhook_secret_token: form.webhook_secret_token,
        consent_required: form.consent_required ? "1" : "0",
        consent_text: form.consent_text,
        consent_confirmed_text: form.consent_confirmed_text,
        followup_reopen_days: form.followup_reopen_days.trim(),
        ...form.texts,
      },
    };
    saveM.mutate(body);
  };

  if (configQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-telegram-page">
        <Spinner />
      </div>
    );
  }

  if (configQ.isError) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-telegram-page">
        {t("admin.telegram.loadError")}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4" data-testid="admin-telegram-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.telegram.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("admin.telegram.description")}</p>
      </div>

      <form onSubmit={onSubmit} className="space-y-4 rounded-lg border border-hairline bg-surface p-4">
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            data-testid="telegram-enabled"
            checked={form.enabled}
            onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            className="rounded border-hairline"
          />
          {t("admin.telegram.enabled")}
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-muted">
              {t("admin.telegram.mode")}
              <HelpPopover title={t("admin.telegram.mode")} testId="telegram-help-mode">
                {t("admin.help.telegram.mode")}
              </HelpPopover>
            </span>
            <SelectField
              items={[
                { value: "polling", label: t("admin.telegram.modePolling") },
                { value: "webhook", label: t("admin.telegram.modeWebhook") },
              ]}
              value={form.mode}
              onChange={(v) => setForm((f) => ({ ...f, mode: v as TelegramMode }))}
              testId="telegram-mode"
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.telegram.queue")}</span>
            <SelectField
              items={[{ value: "", label: "—" }, ...queueOptions]}
              value={form.queue_name}
              onChange={(v) => setForm((f) => ({ ...f, queue_name: v }))}
              testId="telegram-queue"
            />
          </label>

          <label className="block text-sm sm:col-span-2">
            <span className="mb-1 block text-muted">{t("admin.telegram.botToken")}</span>
            <input
              data-testid="telegram-bot-token"
              type="password"
              value={form.bot_token}
              onChange={(e) => setForm((f) => ({ ...f, bot_token: e.target.value }))}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              autoComplete="off"
            />
          </label>

          <label className="block text-sm sm:col-span-2">
            <span className="mb-1 block text-muted">{t("admin.telegram.defaultCustomerUser")}</span>
            <input
              data-testid="telegram-default-customer-user"
              type="text"
              value={form.default_customer_user}
              onChange={(e) => setForm((f) => ({ ...f, default_customer_user: e.target.value }))}
              className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
              autoComplete="off"
            />
          </label>
        </div>

        {form.mode === "webhook" ? (
          <div className="space-y-3 rounded-md border border-hairline bg-surface-subtle p-3">
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.telegram.webhookUrl")}</span>
              <input
                data-testid="telegram-webhook-url"
                type="text"
                value={form.webhook_url}
                onChange={(e) => setForm((f) => ({ ...f, webhook_url: e.target.value }))}
                className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
                autoComplete="off"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("admin.telegram.webhookSecretToken")}</span>
              <input
                data-testid="telegram-webhook-secret-token"
                type="password"
                value={form.webhook_secret_token}
                onChange={(e) => setForm((f) => ({ ...f, webhook_secret_token: e.target.value }))}
                className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
                autoComplete="off"
              />
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="secondary"
                disabled={registerM.isPending}
                onClick={() => {
                  setWebhookMsg(null);
                  registerM.mutate();
                }}
                data-testid="telegram-webhook-register"
              >
                {registerM.isPending
                  ? t("admin.telegram.webhookRegistering")
                  : t("admin.telegram.webhookRegister")}
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={unregisterM.isPending}
                onClick={() => {
                  setWebhookMsg(null);
                  unregisterM.mutate();
                }}
                data-testid="telegram-webhook-unregister"
              >
                {unregisterM.isPending
                  ? t("admin.telegram.webhookUnregistering")
                  : t("admin.telegram.webhookUnregister")}
              </Button>
            </div>
            {webhookMsg ? (
              <p
                data-testid="telegram-webhook-result"
                className={`text-sm ${webhookMsg.ok ? "text-accent" : "text-danger"}`}
              >
                {webhookMsg.text}
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="space-y-3 rounded-md border border-hairline bg-surface-subtle p-3">
          <h2 className="text-sm font-semibold text-ink">{t("admin.telegram.consentTitle")}</h2>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              data-testid="telegram-consent-required"
              checked={form.consent_required}
              onChange={(e) => setForm((f) => ({ ...f, consent_required: e.target.checked }))}
              className="rounded border-hairline"
            />
            {t("admin.telegram.consentRequired")}
          </label>
          <p className="text-xs text-muted">{t("admin.telegram.consentRequiredHelp")}</p>

          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.telegram.consentText")}</span>
            <textarea
              data-testid="telegram-consent-text"
              value={form.consent_text}
              onChange={(e) => setForm((f) => ({ ...f, consent_text: e.target.value }))}
              rows={3}
              className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("admin.telegram.consentConfirmedText")}</span>
            <input
              data-testid="telegram-consent-confirmed-text"
              type="text"
              value={form.consent_confirmed_text}
              onChange={(e) => setForm((f) => ({ ...f, consent_confirmed_text: e.target.value }))}
              className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
            />
          </label>
        </div>

        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("admin.telegram.followupReopenDays")}</span>
          <input
            data-testid="telegram-followup-reopen-days"
            type="number"
            min={0}
            placeholder="7"
            value={form.followup_reopen_days}
            onChange={(e) => setForm((f) => ({ ...f, followup_reopen_days: e.target.value }))}
            className="w-28 rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
          />
          <span className="mt-1 block text-xs text-muted">{t("admin.telegram.followupReopenDaysHelp")}</span>
        </label>

        <div className="space-y-3 rounded-md border border-hairline bg-surface-subtle p-3">
          <h2 className="text-sm font-semibold text-ink">{t("admin.telegram.textsTitle")}</h2>
          <p className="text-xs text-muted">{t("admin.telegram.textsHelp")}</p>
          {TEXT_FIELDS.map((field) => (
            <label key={field.key} className="block text-sm">
              <span className="mb-1 block text-muted">{t(field.label)}</span>
              <textarea
                data-testid={`telegram-text-${field.key}`}
                value={form.texts[field.key]}
                onChange={(e) =>
                  setForm((f) => ({ ...f, texts: { ...f.texts, [field.key]: e.target.value } }))
                }
                rows={field.rows}
                className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
              />
              {field.help ? <span className="mt-1 block text-xs text-muted">{t(field.help)}</span> : null}
            </label>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={saveM.isPending} data-testid="telegram-save">
            {saveM.isPending ? t("admin.telegram.saving") : t("admin.telegram.save")}
          </Button>
          {statusMsg ? (
            <span className="text-sm text-muted" data-testid="telegram-status">
              {statusMsg}
            </span>
          ) : null}
        </div>
      </form>
    </div>
  );
}
