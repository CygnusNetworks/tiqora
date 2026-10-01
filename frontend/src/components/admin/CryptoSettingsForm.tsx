import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError, type CryptoSettingOut, type CryptoSettingsOut } from "@/lib/api";
import { CRYPTO_STATUS_KEY } from "@/components/admin/CryptoStatusBanner";
import { CRYPTO_SETTINGS_KEY, useCryptoSettings } from "@/components/admin/cryptoSettingsQuery";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { SelectField } from "@/components/ui/SelectField";

type Draft = Record<string, boolean | string>;

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function asDraftValue(field: CryptoSettingOut): boolean | string {
  if (field.kind === "bool") return field.value === true;
  return typeof field.value === "string" ? field.value : "";
}

/**
 * Znuny SysConfig parity for one crypto backend, editable in Tiqora. A value
 * from a TIQORA_CRYPTO_* env var or set explicitly in Znuny's SysConfig wins
 * and locks the field; the Tiqora value stays stored for when it is removed.
 */
export function CryptoSettingsForm({ backend }: { backend: "pgp" | "smime" }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const settingsQ = useCryptoSettings();
  const fields = settingsQ.data?.[backend] ?? [];
  const [draft, setDraft] = useState<Draft>({});
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (settingsQ.data) {
      setDraft(Object.fromEntries(settingsQ.data[backend].map((f) => [f.name, asDraftValue(f)])));
    }
  }, [settingsQ.data, backend]);

  const saveM = useMutation({
    mutationFn: (values: Record<string, boolean | string | null>) =>
      api.adminCrypto.settingsUpdate(values),
    onSuccess: async (data: CryptoSettingsOut) => {
      qc.setQueryData(CRYPTO_SETTINGS_KEY, data);
      setNotice({ ok: true, text: t("admin.cryptoSettings.saved") });
      await qc.invalidateQueries({ queryKey: CRYPTO_STATUS_KEY });
      await qc.invalidateQueries({ queryKey: ["admin", "crypto", backend] });
    },
    onError: (err) => setNotice({ ok: false, text: errText(err) }),
  });

  const changed = fields.filter((f) => !f.locked && draft[f.name] !== asDraftValue(f));

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setNotice(null);
    saveM.mutate(Object.fromEntries(changed.map((f) => [f.name, draft[f.name] ?? null])));
  };

  const sourceText = (f: CryptoSettingOut) => {
    switch (f.source) {
      case "env":
        return t("admin.cryptoSettings.source.env", { name: f.env_var ?? "" });
      case "znuny":
        return t("admin.cryptoSettings.source.znuny", { name: f.znuny_setting ?? "" });
      case "tiqora":
        return t("admin.cryptoSettings.source.tiqora");
      case "znuny_default":
        return t("admin.cryptoSettings.source.znunyDefault", { name: f.znuny_setting ?? "" });
      default:
        return t("admin.cryptoSettings.source.default");
    }
  };

  const label = (f: CryptoSettingOut) => t(`admin.cryptoSettings.fields.${f.name}.label`);
  const help = (f: CryptoSettingOut) => t(`admin.cryptoSettings.fields.${f.name}.help`);
  const testId = (f: CryptoSettingOut) => `crypto-setting-${f.name.replace(".", "-")}`;

  const choiceLabel = (c: string) => (c === "" ? t("admin.cryptoSettings.choiceAuto") : c);

  const control = (f: CryptoSettingOut) => {
    const value = draft[f.name];
    if (f.kind === "bool") {
      return (
        <input
          type="checkbox"
          data-testid={testId(f)}
          checked={value === true}
          disabled={f.locked}
          onChange={(e) => setDraft((d) => ({ ...d, [f.name]: e.target.checked }))}
          className="rounded border-hairline"
        />
      );
    }
    if (f.kind === "choice") {
      return (
        <SelectField
          items={(f.choices ?? []).map((c) => ({ value: c, label: choiceLabel(c) }))}
          value={typeof value === "string" ? value : ""}
          onChange={(v) => setDraft((d) => ({ ...d, [f.name]: v }))}
          disabled={f.locked}
          testId={testId(f)}
          aria-label={label(f)}
        />
      );
    }
    return (
      <input
        type="text"
        data-testid={testId(f)}
        value={typeof value === "string" ? value : ""}
        disabled={f.locked}
        onChange={(e) => setDraft((d) => ({ ...d, [f.name]: e.target.value }))}
        className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 font-mono text-sm text-ink disabled:opacity-60"
        autoComplete="off"
        spellCheck={false}
      />
    );
  };

  if (settingsQ.isError) {
    return (
      <p className="text-sm text-danger" data-testid={`crypto-settings-error-${backend}`}>
        {t("admin.cryptoSettings.loadError")}: {errText(settingsQ.error)}
      </p>
    );
  }
  if (!settingsQ.data) return null;

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-3 rounded-lg border border-hairline bg-surface p-3"
      data-testid={`crypto-settings-${backend}`}
    >
      <div>
        <h2 className="text-sm font-semibold text-ink">{t("admin.cryptoSettings.title")}</h2>
        <p className="mt-0.5 text-xs text-muted">{t("admin.cryptoSettings.description")}</p>
      </div>
      <div className="divide-y divide-hairline">
        {fields.map((f) => (
          <div
            key={f.name}
            className="grid gap-x-4 gap-y-1 py-2 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]"
            data-testid={`${testId(f)}-row`}
          >
            <div className="flex items-start gap-1.5 text-sm text-ink">
              <span>{label(f)}</span>
              <HelpPopover title={label(f)} testId={`${testId(f)}-help`}>
                {help(f)}
              </HelpPopover>
            </div>
            <div className="min-w-0 space-y-1">
              <div className={f.kind === "bool" ? "flex items-center" : ""}>{control(f)}</div>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <Badge
                  tone={f.locked ? "warn" : f.source === "tiqora" ? "accent" : "muted"}
                  data-testid={`${testId(f)}-source`}
                >
                  {sourceText(f)}
                </Badge>
                {f.source === "tiqora" ? (
                  <button
                    type="button"
                    className="underline hover:text-ink"
                    disabled={saveM.isPending}
                    onClick={() => {
                      setNotice(null);
                      saveM.mutate({ [f.name]: null });
                    }}
                    data-testid={`${testId(f)}-reset`}
                  >
                    {t("admin.cryptoSettings.reset")}
                  </button>
                ) : null}
                {f.locked && f.tiqora_value !== null && f.tiqora_value !== undefined ? (
                  <span>{t("admin.cryptoSettings.shadowed")}</span>
                ) : null}
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          variant="primary"
          disabled={changed.length === 0 || saveM.isPending}
          data-testid={`crypto-settings-save-${backend}`}
        >
          {saveM.isPending ? t("admin.cryptoSettings.saving") : t("admin.cryptoSettings.save")}
        </Button>
        {notice ? (
          <span
            className={`text-sm ${notice.ok ? "text-green" : "text-danger"}`}
            data-testid={`crypto-settings-notice-${backend}`}
          >
            {notice.text}
          </span>
        ) : null}
      </div>
    </form>
  );
}
