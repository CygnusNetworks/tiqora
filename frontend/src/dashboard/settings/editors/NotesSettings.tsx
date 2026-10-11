import { useId } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { MAX_NOTE_CHARS, charCount } from "../validate";
import type { SettingsEditorProps } from "../registry";

export function NotesSettingsEditor({ value, onChange, errors }: SettingsEditorProps) {
  const { t } = useTranslation();
  const id = useId();
  const markdown = typeof value.markdown === "string" ? value.markdown : "";
  const count = charCount(markdown);
  const error = errors.markdown;
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
        {t("dashboards.settings.notes.label")}
      </label>
      <textarea
        id={id}
        rows={8}
        value={markdown}
        aria-invalid={error ? true : undefined}
        aria-describedby={`${id}-count`}
        onChange={(e) => onChange({ ...value, markdown: e.target.value })}
        className={cn(
          "w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent",
          error && "border-danger focus:ring-danger",
        )}
      />
      <div className="mt-1 flex items-start justify-between gap-3 text-xs">
        <p className="text-muted">{t("dashboards.settings.notes.hint")}</p>
        <span id={`${id}-count`} className={cn("shrink-0 tabular-nums", count > MAX_NOTE_CHARS ? "text-danger" : "text-muted")}>
          {count} / {MAX_NOTE_CHARS}
        </span>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
