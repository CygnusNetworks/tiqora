import { useEffect, useState, type ComponentType, type SVGProps } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError, type CryptoSettingOut, type CryptoSettingsOut } from "@/lib/api";
import {
  CRYPTO_SECTIONS,
  CRYPTO_SETTINGS_KEY,
  CRYPTO_STATUS_KEY,
  REQUIRED_FIELDS,
  useCryptoSettings,
  type CryptoBackend,
} from "@/components/admin/cryptoSettingsQuery";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { FolderIcon, LockIcon, PencilIcon, ServerIcon, ShieldIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";

type Draft = Record<string, boolean | string>;
type IconCmp = ComponentType<SVGProps<SVGSVGElement>>;

const SECTION_ICON: Record<string, IconCmp> = {
  general: ShieldIcon,
  sign: PencilIcon,
  store: FolderIcon,
  env: ServerIcon,
};

/** Choices with at most this many options are a segmented switch, not a dropdown. */
const SEGMENT_MAX = 3;

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function asDraftValue(field: CryptoSettingOut): boolean | string {
  if (field.kind === "bool") return field.value === true;
  return typeof field.value === "string" ? field.value : "";
}

/**
 * The settings tab of the PGP / S-MIME pages: sections with a sub-navigation,
 * saved one section at a time. A value from a TIQORA_CRYPTO_* env var or set
 * explicitly in Znuny's SysConfig wins and locks the field; values changed in
 * Tiqora can be reset. Defaults carry no source note.
 */
export function CryptoSettingsForm({
  backend,
  section,
  onSection,
}: {
  backend: CryptoBackend;
  section: string;
  onSection: (id: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const settingsQ = useCryptoSettings();
  const fields = settingsQ.data?.[backend] ?? [];
  const byName = new Map(fields.map((f) => [f.name, f]));
  const [draft, setDraft] = useState<Draft>({});
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (settingsQ.data) {
      setDraft(Object.fromEntries(settingsQ.data[backend].map((f) => [f.name, asDraftValue(f)])));
    }
  }, [settingsQ.data, backend]);

  const sections = CRYPTO_SECTIONS[backend];
  const current = sections.find((s) => s.id === section) ?? sections[0];
  const sectionFields = current.fields
    .map((n) => byName.get(n))
    .filter((f): f is CryptoSettingOut => f !== undefined);
  const isDirty = (f: CryptoSettingOut) => !f.locked && draft[f.name] !== asDraftValue(f);
  const changed = sectionFields.filter(isDirty);
  const missing = (f: CryptoSettingOut) =>
    REQUIRED_FIELDS[backend].includes(f.name) && (draft[f.name] ?? "") === "";

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

  const save = () => {
    setNotice(null);
    saveM.mutate(Object.fromEntries(changed.map((f) => [f.name, draft[f.name] ?? null])));
  };
  const discard = () => {
    setNotice(null);
    setDraft((d) => ({
      ...d,
      ...Object.fromEntries(sectionFields.map((f) => [f.name, asDraftValue(f)])),
    }));
  };

  const label = (f: CryptoSettingOut) => t(`admin.cryptoSettings.fields.${f.name}.label`);
  const help = (f: CryptoSettingOut) => t(`admin.cryptoSettings.fields.${f.name}.help`);
  const testId = (f: CryptoSettingOut) => `crypto-setting-${f.name.replace(".", "-")}`;
  const choiceLabel = (c: string) =>
    c === ""
      ? t("admin.cryptoSettings.choiceAuto")
      : t(`admin.cryptoSettings.choice.${c}`, { defaultValue: c });

  const source = (f: CryptoSettingOut) => {
    if (isDirty(f)) {
      return (
        <span className="flex items-start gap-1 text-accent">
          <PencilIcon className="mt-px h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0">{t("admin.cryptoSettings.unsaved")}</span>
        </span>
      );
    }
    if (f.source === "env" || f.source === "znuny") {
      return (
        <span className="flex items-start gap-1 text-escalation">
          <LockIcon className="mt-px h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 break-words">
            {f.source === "env"
              ? t("admin.cryptoSettings.source.env", { name: f.env_var ?? "" })
              : t("admin.cryptoSettings.source.znuny", { name: f.znuny_setting ?? "" })}
            {f.tiqora_value !== null && f.tiqora_value !== undefined ? (
              <span className="text-muted"> · {t("admin.cryptoSettings.shadowed")}</span>
            ) : null}
          </span>
        </span>
      );
    }
    if (f.source === "tiqora") {
      return (
        <span className="flex flex-wrap items-center gap-1 text-accent">
          <PencilIcon className="h-3.5 w-3.5 shrink-0" />
          {t("admin.cryptoSettings.source.tiqora")}
          <button
            type="button"
            className="text-muted underline hover:text-ink"
            disabled={saveM.isPending}
            onClick={() => {
              setNotice(null);
              saveM.mutate({ [f.name]: null });
            }}
            data-testid={`${testId(f)}-reset`}
          >
            {t("admin.cryptoSettings.reset")}
          </button>
        </span>
      );
    }
    return null;
  };

  const control = (f: CryptoSettingOut) => {
    const value = draft[f.name];
    if (f.kind === "bool") {
      return (
        <Switch
          checked={value === true}
          disabled={f.locked}
          onChange={(v) => setDraft((d) => ({ ...d, [f.name]: v }))}
          testId={testId(f)}
          aria-label={label(f)}
        />
      );
    }
    const choices = f.choices ?? [];
    if (f.kind === "choice" && choices.length <= SEGMENT_MAX) {
      return (
        <div
          role="radiogroup"
          aria-label={label(f)}
          data-testid={testId(f)}
          className="inline-flex w-fit flex-wrap gap-0.5 rounded-lg border border-hairline bg-surface-subtle p-0.5"
        >
          {choices.map((c) => {
            const on = c === value;
            return (
              <button
                key={c}
                type="button"
                role="radio"
                aria-checked={on}
                disabled={f.locked}
                data-testid={`${testId(f)}-${c}`}
                onClick={() => setDraft((d) => ({ ...d, [f.name]: c }))}
                className={cn(
                  "rounded-md px-3 py-1 text-xs",
                  on
                    ? "bg-surface font-semibold text-ink shadow-sm ring-1 ring-hairline"
                    : "text-muted hover:text-ink",
                )}
              >
                {choiceLabel(c)}
              </button>
            );
          })}
        </div>
      );
    }
    if (f.kind === "choice") {
      return (
        <SelectField
          items={choices.map((c) => ({ value: c, label: choiceLabel(c) }))}
          value={typeof value === "string" ? value : ""}
          onChange={(v) => setDraft((d) => ({ ...d, [f.name]: v }))}
          disabled={f.locked}
          testId={testId(f)}
          aria-label={label(f)}
          className="w-full"
        />
      );
    }
    return (
      <input
        type="text"
        data-testid={testId(f)}
        value={typeof value === "string" ? value : ""}
        disabled={f.locked}
        placeholder={t(`admin.cryptoSettings.fields.${f.name}.placeholder`, { defaultValue: "" })}
        onChange={(e) => setDraft((d) => ({ ...d, [f.name]: e.target.value }))}
        className={cn(
          "w-full rounded-md border bg-surface-subtle px-3 py-1.5 font-mono text-sm text-ink placeholder:text-muted/50 disabled:cursor-not-allowed disabled:opacity-60",
          missing(f) ? "border-danger/60" : "border-hairline",
        )}
        aria-label={label(f)}
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
    <div
      className="grid gap-4 md:grid-cols-[12rem_minmax(0,1fr)] md:items-start"
      data-testid={`crypto-settings-${backend}`}
    >
      <nav className="grid gap-0.5 md:sticky md:top-3" aria-label={t("admin.cryptoPage.tabSettings")}>
        {sections.map((s) => {
          const Icon = SECTION_ICON[s.id] ?? ShieldIcon;
          const on = s.id === current.id;
          const flagged = s.fields.some((n) => {
            const f = byName.get(n);
            return f !== undefined && missing(f);
          });
          return (
            <button
              key={s.id}
              type="button"
              aria-current={on}
              data-testid={`crypto-section-${backend}-${s.id}`}
              onClick={() => onSection(s.id)}
              className={cn(
                "flex items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm",
                on
                  ? "bg-accent-dim font-semibold text-accent"
                  : "text-muted hover:bg-surface-subtle hover:text-ink",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {t(`admin.cryptoSettings.sections.${s.id}.title`)}
              {flagged ? <span className="ml-auto h-1.5 w-1.5 rounded-full bg-escalation" /> : null}
            </button>
          );
        })}
      </nav>
      <section className="rounded-xl border border-hairline bg-surface">
        <header className="px-4 pb-1 pt-4">
          <h2 className="text-base font-semibold text-ink">
            {t(`admin.cryptoSettings.sections.${current.id}.title`)}
          </h2>
          <p className="text-xs text-muted">
            {t(`admin.cryptoSettings.sections.${current.id}.description`)}
          </p>
        </header>
        <div className="divide-y divide-hairline">
          {sectionFields.map((f) => (
            <div
              key={f.name}
              className="grid gap-x-6 gap-y-2 px-4 py-3.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,20rem)] sm:items-center"
              data-testid={`${testId(f)}-row`}
            >
              <div className="min-w-0">
                <div className="text-sm font-medium text-ink">{label(f)}</div>
                <p className="text-xs text-muted">{help(f)}</p>
              </div>
              <div
                className={cn(
                  "grid min-w-0 gap-1 text-xs",
                  f.kind === "bool" ? "sm:justify-items-end" : "justify-items-stretch",
                )}
              >
                {control(f)}
                <div data-testid={`${testId(f)}-source`}>{source(f)}</div>
                {missing(f) && !isDirty(f) ? (
                  <span className="text-danger">{t("admin.cryptoSettings.required")}</span>
                ) : null}
              </div>
            </div>
          ))}
        </div>
        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-hairline px-4 py-3">
          {notice ? (
            <span
              className={cn("mr-auto text-sm", notice.ok ? "text-green" : "text-danger")}
              data-testid={`crypto-settings-notice-${backend}`}
            >
              {notice.text}
            </span>
          ) : null}
          <Button
            variant="ghost"
            onClick={discard}
            disabled={changed.length === 0 || saveM.isPending}
          >
            {t("admin.cryptoSettings.discard")}
          </Button>
          <Button
            variant="primary"
            onClick={save}
            disabled={changed.length === 0 || saveM.isPending}
            data-testid={`crypto-settings-save-${backend}`}
          >
            {saveM.isPending
              ? t("admin.cryptoSettings.saving")
              : t("admin.cryptoSettings.saveSection")}
          </Button>
        </footer>
      </section>
    </div>
  );
}
