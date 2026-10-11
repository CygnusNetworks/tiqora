import { useId, useLayoutEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { SchemaForm } from "@/components/fields/SchemaForm";
import type { FieldValues } from "@/components/fields/types";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import type { WidgetDefinition } from "@/plugins/dashboardWidgets";
import { settingsWithDefaults } from "../definitions";
import { widgetTexts } from "../gallery/galleryModel";
import { presetOf } from "../widgets/presets";
import type { EditWidget } from "../model";
import { CORE_SETTINGS_EDITORS, formAdapterFor } from "./registry";
import { SettingsPreview } from "./SettingsPreview";
import { MAX_TITLE_CHARS, finalizeSettings, validateCore, validateSchemaValues } from "./validate";

const SERVER_PREFIX = "settings.";

/** Server errors by field: `settings.limit` -> `limit`; the rest is a general message. */
function splitServerErrors(errors: Record<string, string> | undefined): {
  fields: Record<string, string>;
  general: string[];
} {
  const fields: Record<string, string> = {};
  const general: string[] = [];
  for (const [path, message] of Object.entries(errors ?? {})) {
    if (path.startsWith(SERVER_PREFIX) && path.length > SERVER_PREFIX.length) fields[path.slice(SERVER_PREFIX.length)] = message;
    else general.push(message);
  }
  return { fields, general };
}

/** Mounts the dialog in its own body-level host and makes the rest of the page inert while open. */
function useModalHost(): HTMLElement | null {
  const [host, setHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const el = document.createElement("div");
    document.body.appendChild(el);
    const inerted: Element[] = [];
    for (const child of Array.from(document.body.children)) {
      if (child === el || child.hasAttribute("inert") || child.tagName === "SCRIPT") continue;
      child.setAttribute("inert", "");
      inerted.push(child);
    }
    setHost(el);
    return () => {
      for (const child of inerted) child.removeAttribute("inert");
      el.remove();
      // The page was inert while the opener had focus, which dropped it.
      if (opener && opener !== document.body && opener.isConnected) opener.focus();
    };
  }, []);
  return host;
}

/**
 * Settings of one placed widget in a side drawer: title, a form for the type (bespoke editor for
 * ticket list, links and notes; generated from `settingsSchema` otherwise) and a live preview.
 * Nothing is saved here: "Übernehmen" hands the draft to the edit session (one undo step).
 */
export function SettingsDrawer({
  widget,
  def,
  errors,
  focusSource = false,
  onApply,
  onCancel,
}: {
  widget: EditWidget;
  def: WidgetDefinition;
  /** Server (422) messages by path (`settings.limit`); others show as a general message. */
  errors?: Record<string, string>;
  /** Put the focus in the source section on open (the widget was opened from its "Suche wählen" action). */
  focusSource?: boolean;
  onApply: (settings: Record<string, unknown>, titleOverride: string | null) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const host = useModalHost();
  const uid = useId();
  const typeName = widgetTexts(def, t).title;
  const adapter = useMemo(() => formAdapterFor(def), [def]);
  const Editor = CORE_SETTINGS_EDITORS[def.key];

  // Older stored settings are migrated and completed once, on open.
  const [draft, setDraft] = useState(() => settingsWithDefaults(def, widget.settings, widget.type_version));
  const [title, setTitle] = useState(widget.title_override ?? "");
  const [server, setServer] = useState(() => splitServerErrors(errors));

  const change = (next: Record<string, unknown>) => {
    // A field the user touches is no longer "wrong because the server said so".
    const touched = Object.keys({ ...draft, ...next }).filter((k) => JSON.stringify(draft[k]) !== JSON.stringify(next[k]));
    if (touched.length > 0) {
      setServer((s) => {
        const fields = Object.fromEntries(
          Object.entries(s.fields).filter(([path]) => !touched.some((k) => path === k || path.startsWith(`${k}.`))),
        );
        return Object.keys(fields).length === Object.keys(s.fields).length ? s : { ...s, fields };
      });
    }
    setDraft(next);
  };

  // The title an empty field falls back to: the preset's name for ticket lists and counters.
  const preset = def.key === "core.ticket-list" || def.key === "core.counter" ? presetOf(draft.source) : null;
  const defaultTitle = preset ? t(`dashboards.presets.${preset}.${def.key === "core.counter" ? "counter" : "list"}`) : typeName;

  const clientErrors = useMemo(() => {
    if (Editor) return validateCore(def.key, draft, t);
    return adapter ? validateSchemaValues(adapter.schema, adapter.toForm(draft), t) : {};
  }, [Editor, adapter, def.key, draft, t]);
  const invalid = Object.keys(clientErrors).length > 0;
  const fieldErrors = { ...server.fields, ...clientErrors };

  const apply = () => {
    if (invalid) return;
    const trimmed = title.trim();
    onApply(finalizeSettings(def.key, draft), trimmed === "" ? null : trimmed);
  };

  if (!host) return null;
  return createPortal(
    <Dialog
      open
      placement="right"
      size="lg"
      title={t("dashboards.settings.title")}
      description={typeName}
      closeLabel={t("dashboards.edit.close")}
      onClose={onCancel}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onCancel}>
            {t("dashboards.settings.cancel")}
          </Button>
          <Button variant="primary" size="sm" disabled={invalid} onClick={apply}>
            {t("dashboards.settings.apply")}
          </Button>
        </>
      }
    >
      <form
        className="space-y-5"
        onSubmit={(e) => {
          e.preventDefault();
          apply();
        }}
      >
        {server.general.map((m) => (
          <p key={m} role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
            {m}
          </p>
        ))}
        <div>
          <label htmlFor={`${uid}-title`} className="mb-1 block text-xs font-medium text-muted">
            {t("dashboards.settings.titleField")}
          </label>
          <input
            id={`${uid}-title`}
            value={title}
            maxLength={MAX_TITLE_CHARS}
            placeholder={defaultTitle}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
          />
          <p className="mt-1 text-xs text-muted">{t("dashboards.settings.titleHint")}</p>
        </div>

        {Editor ? (
          <Editor value={draft} onChange={change} errors={fieldErrors} focusSource={focusSource} />
        ) : adapter ? (
          <SchemaForm
            schema={adapter.schema}
            i18nPrefix={adapter.i18nPrefix}
            values={adapter.toForm(draft) as FieldValues}
            errors={fieldErrors}
            onChange={(next) => change(adapter.fromForm(next, draft))}
          />
        ) : null}

        <SettingsPreview
          def={def}
          settings={draft}
          valid={!invalid}
          titleOverride={title}
          size={{ w: widget.layout.lg.w, h: widget.layout.lg.h }}
        />
      </form>
    </Dialog>,
    host,
  );
}
