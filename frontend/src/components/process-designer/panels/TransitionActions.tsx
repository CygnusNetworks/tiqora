import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import type { DesignerEntity, EntitySummary } from "@/lib/processDesignerApi";
import { moveItem } from "./activityModel";

/**
 * Section "Aktionen" of a transition: the ordered list of actions it runs. Order, adding and removing
 * change the path (saved with the canvas via `setActions`); the actions themselves are entities
 * edited in the `ActionPanel`.
 */
export function TransitionActions({
  actionIds,
  actions,
  library,
  adoptAction,
  selectedId,
  createAction,
  onChange,
  onSelect,
}: {
  actionIds: string[];
  actions: DesignerEntity[];
  /** Every action of all processes (shared entities); without it only `actions` can be added. */
  library?: EntitySummary[];
  /** Loads a picked action of another process into the draft before it is added. */
  adoptAction?: (id: string) => Promise<void>;
  selectedId?: string | null;
  /** Creates a new empty action and returns its entity id. */
  createAction: () => Promise<string>;
  onChange: (ids: string[]) => void;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(false);
  const nameOf = new Map([...(library ?? []), ...actions].map((a) => [a.entity_id, a.name]));
  const addable = (library ?? actions).filter((a) => !actionIds.includes(a.entity_id));

  const addExisting = async (id: string) => {
    setError(false);
    try {
      await adoptAction?.(id);
      onChange([...actionIds, id]);
    } catch {
      setError(true);
    }
  };

  const addNew = async () => {
    setCreating(true);
    setError(false);
    try {
      const id = await createAction();
      onChange([...actionIds, id]);
      onSelect(id);
    } catch {
      setError(true);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-2" data-testid="transition-actions">
      <h3 className="text-xs font-medium text-muted">{t("admin.processDesigner.action.title")}</h3>
      {actionIds.length === 0 ? (
        <p className="text-sm text-muted">{t("admin.processDesigner.action.none")}</p>
      ) : (
        <ol className="space-y-1" data-testid="transition-actions-list">
          {actionIds.map((id, i) => {
            const label = nameOf.get(id) ?? id;
            return (
              <li
                key={id}
                data-testid={`transition-action-${id}`}
                className={`flex items-center gap-1 rounded-md border px-2 py-1 text-sm text-ink ${
                  id === selectedId ? "border-accent" : "border-hairline"
                }`}
              >
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate text-left hover:underline"
                  onClick={() => onSelect(id)}
                  data-testid={`transition-action-${id}-edit`}
                >
                  {label}
                </button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={i === 0}
                  aria-label={t("admin.processDesigner.action.moveUp", { name: label })}
                  onClick={() => onChange(moveItem(actionIds, i, -1))}
                  data-testid={`transition-action-${id}-up`}
                >
                  ↑
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={i === actionIds.length - 1}
                  aria-label={t("admin.processDesigner.action.moveDown", { name: label })}
                  onClick={() => onChange(moveItem(actionIds, i, 1))}
                  data-testid={`transition-action-${id}-down`}
                >
                  ↓
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={t("admin.processDesigner.action.remove", { name: label })}
                  onClick={() => onChange(actionIds.filter((x) => x !== id))}
                  data-testid={`transition-action-${id}-remove`}
                >
                  ×
                </Button>
              </li>
            );
          })}
        </ol>
      )}
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {t("admin.processDesigner.createError")}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {addable.length > 0 ? (
          <SelectField
            testId="transition-actions-add"
            aria-label={t("admin.processDesigner.reuse")}
            value={null}
            placeholder={t("admin.processDesigner.reuse")}
            items={addable.map((a) => ({ value: a.entity_id, label: a.name }))}
            onChange={(id) => void addExisting(id)}
          />
        ) : null}
        <Button size="sm" disabled={creating} onClick={() => void addNew()} data-testid="transition-actions-new">
          {t("admin.processDesigner.newAction")}
        </Button>
      </div>
    </div>
  );
}
