import { useTranslation } from "react-i18next";
import { Switch } from "@/components/ui/Switch";
import {
  RATE_LIMIT_BOUNDS,
  UPLOAD_TYPES,
  MAX_UPLOAD_MB,
  type RateLimitKey, setValues } from "@/lib/webchatForm";
import { Field, NumberInput, type TabProps } from "./shared";

const RATE_KEYS = Object.keys(RATE_LIMIT_BOUNDS) as RateLimitKey[];

export function ProtectionTab({ draft, errors, onDraft }: TabProps) {
  const { t } = useTranslation();
  const v = draft.values;
  const up = v.upload_policy;
  const setUpload = (patch: Partial<typeof up>) => onDraft(setValues(draft, { upload_policy: { ...up, ...patch } }));
  const mb = Math.round(up.max_bytes / 1024 / 1024);
  return (
    <div className="space-y-6" data-testid="webchat-tab-protection">
      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.protection.limits")}</h3>
          <p className="text-xs text-muted">{t("webchat.admin.protection.limitsHint")}</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {RATE_KEYS.map((k) => {
            const [min, max] = RATE_LIMIT_BOUNDS[k];
            return (
              <Field
                key={k}
                id={`rate-${k}`}
                label={t(`webchat.admin.protection.rate.${k}`)}
                hint={t("webchat.admin.protection.range", { min, max })}
                error={errors[`rate_limits.${k}`]}
              >
                <NumberInput
                  id={`rate-${k}`}
                  min={min}
                  max={max}
                  value={v.rate_limits[k]}
                  error={errors[`rate_limits.${k}`]}
                  onChange={(n) => onDraft(setValues(draft, { rate_limits: { ...v.rate_limits, [k]: n } }))}
                />
              </Field>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.protection.uploads")}</h3>
        <label className="flex items-center gap-2 text-sm text-ink">
          <Switch
            checked={up.enabled}
            onChange={(enabled) => setUpload({ enabled })}
            testId="webchat-upload-enabled"
            aria-label={t("webchat.admin.protection.uploadsAllow")}
          />
          {t("webchat.admin.protection.uploadsAllow")}
        </label>
        <Field id="upload-mb" label={t("webchat.admin.protection.maxMb")} hint={t("webchat.admin.protection.range", { min: 1, max: MAX_UPLOAD_MB })} error={errors["upload_policy.max_bytes"]} className="max-w-xs">
          <NumberInput
            id="upload-mb"
            min={1}
            max={MAX_UPLOAD_MB}
            value={mb}
            error={errors["upload_policy.max_bytes"]}
            onChange={(n) => setUpload({ max_bytes: Number.isFinite(n) ? n * 1024 * 1024 : NaN })}
          />
        </Field>
        <fieldset>
          <legend className="mb-1 text-xs font-medium text-muted">{t("webchat.admin.protection.types")}</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {UPLOAD_TYPES.map((type) => (
              <label key={type} className="flex items-center gap-1.5 text-sm text-ink">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-accent"
                  data-testid={`webchat-upload-type-${type}`}
                  checked={up.types.includes(type)}
                  onChange={(e) =>
                    setUpload({ types: e.target.checked ? [...up.types, type] : up.types.filter((x) => x !== type) })
                  }
                />
                {t(`webchat.admin.protection.type.${type}`)}
              </label>
            ))}
          </div>
          {errors["upload_policy.types"] && (
            <p role="alert" className="mt-1 text-xs text-danger">
              {errors["upload_policy.types"]}
            </p>
          )}
        </fieldset>
      </section>

      <Field id="retention" label={t("webchat.admin.protection.retention")} hint={t("webchat.admin.protection.retentionHint")} error={errors.retention_days} className="max-w-xs">
        <NumberInput
          id="retention"
          min={1}
          max={3650}
          value={v.retention_days}
          error={errors.retention_days}
          onChange={(retention_days) => onDraft(setValues(draft, { retention_days }))}
        />
      </Field>
    </div>
  );
}
