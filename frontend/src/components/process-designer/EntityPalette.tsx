import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { DesignerEntity } from "@/lib/processDesignerApi";

/** MIME type of a palette drag: the payload is an activity entity id, or "" for a new step. */
export const PALETTE_DRAG_TYPE = "application/x-tiqora-activity";

/** Left column: "Neuer Schritt" plus the existing activities that are not part of the process yet. */
export function EntityPalette({ activities, usedIds }: { activities: DesignerEntity[]; usedIds: Set<string> }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const free = activities.filter((a) => !usedIds.has(a.entity_id) && (!q || a.name.toLowerCase().includes(q)));

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
          <ul className="space-y-1.5">
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
    </div>
  );
}
