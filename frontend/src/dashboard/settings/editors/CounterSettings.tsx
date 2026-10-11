import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { SettingsEditorProps } from "../registry";
import { FIELD, SourceFields } from "./SourceFields";

const TONES = ["neutral", "danger", "ok"] as const;

/** Counter: the same sources as the ticket list (saved search or own filter) and the colour of the number. */
export function CounterSettingsEditor({ value, onChange, errors, focusSource }: SettingsEditorProps) {
  const { t } = useTranslation();
  const uid = useId();
  const tone = (TONES as readonly unknown[]).includes(value.tone) ? String(value.tone) : "neutral";
  return (
    <div className="space-y-5">
      <SourceFields value={value} onChange={onChange} errors={errors} focusSource={focusSource} />
      <div>
        <label htmlFor={`${uid}-tone`} className="mb-1 block text-xs font-medium text-muted">
          {t("dashboards.settings.counter.tone")}
        </label>
        <select
          id={`${uid}-tone`}
          value={tone}
          onChange={(e) => onChange({ ...value, tone: e.target.value })}
          className={FIELD}
        >
          {TONES.map((x) => (
            <option key={x} value={x}>
              {t(`dashboards.settings.forms.counter.fields.tone.options.${x}`)}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
