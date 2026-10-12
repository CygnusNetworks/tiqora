import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { EntitySummary, SubEntityType } from "@/lib/processDesignerApi";

/** MIME type of a palette drag: the payload is an activity entity id, or "" for a new step. */
export const PALETTE_DRAG_TYPE = "application/x-tiqora-activity";

const TYPE_LABEL: Record<SubEntityType, string> = {
  Activity: "admin.processDesigner.panelActivity.title",
  ActivityDialog: "admin.processDesigner.dialog.title",
  Transition: "admin.processDesigner.transitionLabel",
  TransitionAction: "admin.processDesigner.panelAction.title",
};

/**
 * Left column: "Neuer Schritt", the existing steps of all processes that this process does not use
 * yet (shared Znuny entities, dragged in like Znuny's activity list), and the entries no process
 * uses any more, which can be deleted here.
 */
export function EntityPalette({
  activities,
  usedIds,
  unused = [],
  onDelete,
}: {
  /** Every step (`GET /entities/Activity`). */
  activities: EntitySummary[];
  /** The steps already on the canvas. */
  usedIds: Set<string>;
  /** Steps, forms, transitions and actions that nothing uses (`used_by` empty). */
  unused?: EntitySummary[];
  onDelete?: (entity: EntitySummary) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const free = activities.filter((a) => !usedIds.has(a.entity_id) && (!q || a.name.toLowerCase().includes(q)));
  const orphans = unused.filter((e) => !usedIds.has(e.entity_id));

  const dragStart = (id: string) => (e: React.DragEvent) => {
    e.dataTransfer.setData(PALETTE_DRAG_TYPE, id);
    e.dataTransfer.effectAllowed = "copy";
  };
  const itemClass =
    "cursor-grab rounded-md border border-hairline bg-surface-subtle px-2 py-1.5 text-sm text-ink active:cursor-grabbing";

  return (
    <div className="space-y-3" data-testid="process-designer-entity-palette">
      <div draggable onDragStart={dragStart("")} className={itemClass} data-testid="process-designer-palette-new">
        + {t("admin.processDesigner.newStep")}
      </div>
      <div>
        <h3 className="mb-1 text-xs font-medium text-muted">{t("admin.processDesigner.reuse")}</h3>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("admin.processDesigner.search")}
          aria-label={t("admin.processDesigner.search")}
          className="mb-2 w-full rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink"
        />
        {free.length === 0 ? (
          <p className="text-xs text-muted">{t("admin.processDesigner.noSteps")}</p>
        ) : (
          <ul className="max-h-64 space-y-1.5 overflow-auto">
            {free.map((a) => (
              <li
                key={a.entity_id}
                draggable
                onDragStart={dragStart(a.entity_id)}
                className={itemClass}
                data-testid={`process-designer-palette-${a.entity_id}`}
              >
                {a.name}
              </li>
            ))}
          </ul>
        )}
      </div>
      {onDelete && orphans.length > 0 ? (
        <div data-testid="process-designer-palette-unused">
          <h3 className="mb-1 text-xs font-medium text-muted">{t("admin.processDesigner.unused")}</h3>
          <ul className="max-h-48 space-y-1 overflow-auto">
            {orphans.map((e) => (
              <li key={e.entity_id} className="flex items-center gap-1 text-sm text-ink">
                <span className="min-w-0 flex-1 truncate" title={e.entity_id}>
                  <span className="text-xs text-muted">{t(TYPE_LABEL[e.entity_type as SubEntityType])}: </span>
                  {e.name}
                </span>
                <button
                  type="button"
                  onClick={() => onDelete(e)}
                  aria-label={t("admin.processDesigner.deleteEntity", { name: e.name })}
                  data-testid={`process-designer-delete-${e.entity_id}`}
                  className="rounded px-1.5 text-muted hover:bg-surface-subtle hover:text-danger"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
