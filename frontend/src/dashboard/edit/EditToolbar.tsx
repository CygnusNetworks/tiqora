import { forwardRef } from "react";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { cn } from "@/lib/cn";
import { formatTimeOfDay } from "@/lib/format";

const BTN =
  "inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg text-[13px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40";
const OUTLINE = "border border-surface/25 bg-transparent text-surface hover:bg-surface/10 disabled:hover:bg-transparent";

function UndoIcon({ redo = false }: { redo?: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" className={cn(redo && "-scale-x-100")}>
      <path
        d="M6 4 2 8l4 4M2.5 8H10a4 4 0 0 1 0 8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export interface EditToolbarProps {
  name: string;
  /** Last successful draft write (nothing is shown while storage is blocked). */
  draftSavedAt: number | null;
  /** `savedAt` of a draft from an earlier session that can be restored. */
  restoreAt: number | null;
  /** That draft was kept after a save conflict: restoring and saving replaces the newer version seen at this time. */
  restoreReplacesAt?: number | null;
  onRestore: () => void;
  onDropDraft: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onDiscard: () => void;
  onSave: () => void;
  saving: boolean;
  /** A failed save that names no widget (too tall, server message, network). */
  error: string | null;
  /** "Widget hinzufügen": shows or hides the widget gallery (absent on phones, where there is none). */
  gallery?: { open: boolean; onToggle: () => void };
}

/**
 * The dark edit bar (inverse tokens): "{name} bearbeiten", draft state, undo/redo, "Verwerfen"
 * and "Speichern". Sticky at the top while the grid scrolls.
 */
export const EditToolbar = forwardRef<HTMLDivElement, EditToolbarProps>(function EditToolbar(
  {
    name,
    draftSavedAt,
    restoreAt,
    restoreReplacesAt = null,
    onRestore,
    onDropDraft,
    canUndo,
    canRedo,
    onUndo,
    onRedo,
    onDiscard,
    onSave,
    saving,
    error,
    gallery,
  },
  ref,
) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const time = (at: number) => formatTimeOfDay(new Date(at), locale);
  return (
    <div
      ref={ref}
      role="toolbar"
      aria-label={t("dashboards.edit.toolbar")}
      tabIndex={-1}
      className="sticky top-0 z-30 flex flex-wrap items-center gap-3 rounded-[14px] bg-ink px-4 py-3 text-surface shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:px-6"
    >
      <span className="min-w-0 truncate text-sm font-semibold">{t("dashboards.edit.title", { name })}</span>
      {draftSavedAt !== null && (
        <span className="text-xs text-surface/70">{t("dashboards.edit.draftSaved", { time: time(draftSavedAt) })}</span>
      )}
      {restoreAt !== null && (
        <span className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={onRestore} className={cn(BTN, OUTLINE, "px-3 py-1.5 text-xs font-medium")}>
            {restoreReplacesAt !== null
              ? t("dashboards.edit.restoreOverwrite", { time: time(restoreAt), newer: time(restoreReplacesAt) })
              : t("dashboards.edit.restoreDraft", { time: time(restoreAt) })}
          </button>
          <button
            type="button"
            onClick={onDropDraft}
            className={cn(BTN, "px-2 py-1.5 text-xs text-surface/70 underline-offset-2 hover:text-surface hover:underline")}
          >
            {t("dashboards.edit.dropDraft")}
          </button>
        </span>
      )}
      {error && (
        <span role="alert" className="rounded-md bg-danger px-2.5 py-1 text-xs font-medium text-surface">
          {error}
        </span>
      )}
      <span className="flex-1" />
      <span className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          aria-label={t("dashboards.edit.undo")}
          title={t("dashboards.edit.undo")}
          disabled={!canUndo}
          onClick={onUndo}
          className={cn(BTN, OUTLINE, "px-2.5")}
        >
          <UndoIcon />
        </button>
        <button
          type="button"
          aria-label={t("dashboards.edit.redo")}
          title={t("dashboards.edit.redo")}
          disabled={!canRedo}
          onClick={onRedo}
          className={cn(BTN, OUTLINE, "px-2.5")}
        >
          <UndoIcon redo />
        </button>
        {gallery && (
          <button
            type="button"
            aria-expanded={gallery.open}
            onClick={gallery.onToggle}
            className={cn(BTN, OUTLINE, "px-3.5", gallery.open && "bg-surface/15")}
          >
            {t("dashboards.gallery.open")}
          </button>
        )}
        <button type="button" onClick={onDiscard} disabled={saving} className={cn(BTN, OUTLINE, "px-3.5")}>
          {t("dashboards.edit.discard")}
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={saving}
          aria-busy={saving || undefined}
          className={cn(BTN, "bg-accent px-4 font-semibold text-accent-ink hover:bg-accent/90 disabled:hover:bg-accent")}
        >
          {saving ? t("dashboards.edit.saving") : t("dashboards.edit.save")}
        </button>
      </span>
    </div>
  );
});
