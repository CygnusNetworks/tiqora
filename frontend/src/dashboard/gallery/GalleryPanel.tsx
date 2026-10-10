import { forwardRef, useMemo, useState, type DragEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { useOptionalAuth } from "@/auth/AuthContext";
import { cn } from "@/lib/cn";
import { useDashboardWidgetDefinitions, type WidgetDefinition } from "@/plugins/dashboardWidgets";
import { canUseWidget } from "../definitions";
import { MAX_WIDGETS_PER_DASHBOARD } from "../model";
import { buildGallery, glyphOf, toneOf, widgetTexts, WIDGET_DRAG_TYPE, type GlyphTone } from "./galleryModel";

const TONE_CLASS: Record<GlyphTone, string> = {
  accent: "bg-accent/10 text-accent",
  green: "bg-green/10 text-green",
  danger: "bg-danger/10 text-danger",
  amber: "bg-amber/10 text-amber",
  purple: "bg-purple/10 text-purple",
  muted: "bg-muted/10 text-muted",
};

const FOCUS = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export interface GalleryPanelProps {
  /** Widgets on the dashboard right now (the limit disables every card). */
  widgetCount: number;
  /** Click or Enter on a card: `at` is undefined, so the widget lands in the first free spot. */
  onAdd: (def: WidgetDefinition, at?: { x: number; y: number }) => void;
  onClose: () => void;
  className?: string;
}

/**
 * "Widget hinzufügen": the search box and the widgets grouped as Tickets / Für mich / Team.
 * A card is a button (click or Enter adds it in the first free spot) and, for the pointer, also
 * draggable onto the grid (the drag carries the widget key). Escape closes the panel.
 */
export const GalleryPanel = forwardRef<HTMLInputElement, GalleryPanelProps>(function GalleryPanel(
  { widgetCount, onAdd, onClose, className },
  searchRef,
) {
  const { t } = useTranslation();
  const user = useOptionalAuth()?.user;
  const defs = useDashboardWidgetDefinitions();
  const [query, setQuery] = useState("");
  const full = widgetCount >= MAX_WIDGETS_PER_DASHBOARD;
  const isAdmin = user?.is_admin ?? false;
  const canViewReports = user?.can_view_reports ?? false;
  const groups = useMemo(
    () =>
      buildGallery(defs, query, t, (def) => canUseWidget(def, { is_admin: isAdmin, can_view_reports: canViewReports })),
    [defs, query, t, isAdmin, canViewReports],
  );

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    e.preventDefault();
    e.stopPropagation();
    onClose();
  };
  const onDragStart = (e: DragEvent, def: WidgetDefinition) => {
    if (full) {
      e.preventDefault();
      return;
    }
    e.dataTransfer.setData(WIDGET_DRAG_TYPE, def.key);
    e.dataTransfer.effectAllowed = "copy";
  };

  return (
    <aside
      aria-label={t("dashboards.gallery.title")}
      onKeyDown={onKeyDown}
      className={cn("flex flex-col gap-4 rounded-[14px] border border-hairline bg-surface p-5", className)}
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-ink">{t("dashboards.gallery.title")}</h2>
        <button
          type="button"
          aria-label={t("dashboards.gallery.close")}
          onClick={onClose}
          className={cn("h-[30px] w-[30px] rounded-lg border border-hairline bg-surface text-ink hover:bg-surface-subtle", FOCUS)}
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        {t("dashboards.gallery.search")}
        <input
          ref={searchRef}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("dashboards.gallery.searchPlaceholder")}
          className={cn("rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink", FOCUS)}
        />
      </label>
      {full && (
        <p className="rounded-lg bg-warn/10 px-3 py-2 text-xs text-ink">
          {t("dashboards.gallery.limit", { max: MAX_WIDGETS_PER_DASHBOARD })}
        </p>
      )}
      {groups.length === 0 ? (
        <div className="flex flex-col items-start gap-2 py-2 text-sm text-muted">
          <p>{t("dashboards.gallery.empty")}</p>
          {query !== "" && (
            <button
              type="button"
              onClick={() => setQuery("")}
              className={cn("rounded-md text-accent underline underline-offset-2", FOCUS)}
            >
              {t("dashboards.gallery.clearSearch")}
            </button>
          )}
        </div>
      ) : (
        groups.map((g) => (
          <section key={g.id} className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
              {t(`dashboards.gallery.groups.${g.id}`)}
            </h3>
            <ul className="flex flex-col gap-2">
              {g.items.map((def) => {
                const { title, description } = widgetTexts(def, t);
                return (
                  <li
                    key={def.key}
                    draggable={!full}
                    onDragStart={(e) => onDragStart(e, def)}
                    className={cn(!full && "cursor-grab")}
                  >
                    <button
                      type="button"
                      disabled={full}
                      onClick={() => onAdd(def, undefined)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-[10px] border border-hairline bg-surface px-3 py-2.5 text-left transition-colors hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-surface",
                        FOCUS,
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "flex h-[34px] w-[34px] flex-none items-center justify-center rounded-lg text-[13px] font-bold",
                          TONE_CLASS[toneOf(def)],
                        )}
                      >
                        {glyphOf(def)}
                      </span>
                      <span className="flex min-w-0 flex-col gap-0.5">
                        <span className="text-sm font-semibold text-ink">{title}</span>
                        {description && <span className="text-xs text-muted">{description}</span>}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}
      <p className="text-xs text-muted">{t("dashboards.gallery.hint")}</p>
    </aside>
  );
});
