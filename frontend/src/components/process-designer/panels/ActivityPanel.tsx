import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { processDesignerApi, type DesignerEntity, type EntitySummary } from "@/lib/processDesignerApi";
import { dialogIdsOf, moveItem, renumberDialogs } from "./activityModel";
import { PanelFrame } from "./PanelFrame";

const inputCls = "w-full rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink";

/**
 * Step properties: name and ordered forms. Saves itself (`PUT /entities/Activity/{eid}`), because a
 * step is an entity of its own that several processes may share. Keyed by entity id + version by the
 * caller, so a reload resets the local edits.
 */
export function ActivityPanel({
  activity,
  dialogs,
  library,
  adoptDialog,
  createDialog,
  onSaved,
  onReload,
  onOpenForm,
  openFormId,
}: {
  activity: DesignerEntity;
  dialogs: DesignerEntity[];
  /** Every form of all processes (shared entities); without it only `dialogs` can be added. */
  library?: EntitySummary[];
  /** Loads a picked form of another process into the draft before it is added. */
  adoptDialog?: (id: string) => Promise<void>;
  /** Creates a new empty form and returns its entity id. */
  createDialog: () => Promise<string>;
  onSaved: (saved: DesignerEntity) => void;
  onReload: () => void;
  /** Opens the form's own panel (name, fields, defaults) below this one. */
  onOpenForm?: (id: string) => void;
  openFormId?: string | null;
}) {
  const { t } = useTranslation();
  const storedIds = dialogIdsOf(activity.config);
  const [name, setName] = useState(activity.name);
  const [ids, setIds] = useState(storedIds);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState(false);
  const [creating, setCreating] = useState(false);

  const dialogName = new Map([...(library ?? []), ...dialogs].map((d) => [d.entity_id, d.name]));
  const dirty = name !== activity.name || ids.join("|") !== storedIds.join("|");
  const addable = (library ?? dialogs).filter((d) => !ids.includes(d.entity_id));

  const addExisting = async (id: string) => {
    setError(false);
    try {
      await adoptDialog?.(id);
      setIds((cur) => (cur.includes(id) ? cur : [...cur, id]));
    } catch {
      setError(true);
    }
  };

  const save = async () => {
    setSaving(true);
    setError(false);
    try {
      const saved = await processDesignerApi.updateEntity("Activity", activity.entity_id, {
        expected_version: activity.version,
        name: name.trim() || activity.name,
        config: { ...activity.config, ActivityDialog: renumberDialogs(ids) },
      });
      setConflict(false);
      onSaved(saved);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setConflict(true);
      else setError(true);
    } finally {
      setSaving(false);
    }
  };

  const addNew = async () => {
    setCreating(true);
    setError(false);
    try {
      const id = await createDialog();
      setIds((cur) => [...cur, id]);
    } catch {
      setError(true);
    } finally {
      setCreating(false);
    }
  };

  return (
    <PanelFrame
      title={t("admin.processDesigner.panelActivity.title")}
      testId="activity-panel"
      dirty={dirty}
      saving={saving}
      conflict={conflict}
      error={error}
      onSave={() => void save()}
      onDiscard={() => {
        setName(activity.name);
        setIds(storedIds);
      }}
      onReload={onReload}
    >
      <label className="block space-y-1 text-xs text-muted">
        {t("admin.processDesigner.panelActivity.name")}
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className={inputCls}
          data-testid="activity-panel-name"
        />
      </label>
      <div className="space-y-2">
        <h3 className="text-xs font-medium text-muted">{t("admin.processDesigner.panelActivity.forms")}</h3>
        {ids.length === 0 ? (
          <p className="text-sm text-muted">{t("admin.processDesigner.panelActivity.noForms")}</p>
        ) : (
          <ol className="space-y-1" data-testid="activity-panel-forms">
            {ids.map((id, i) => {
              const label = dialogName.get(id) ?? id;
              return (
                <li
                  key={id}
                  data-testid={`activity-panel-form-${id}`}
                  className="flex items-center gap-1 rounded-md border border-hairline px-2 py-1 text-sm text-ink"
                >
                  {onOpenForm ? (
                    <button
                      type="button"
                      onClick={() => onOpenForm(id)}
                      aria-pressed={openFormId === id}
                      data-testid={`activity-panel-open-${id}`}
                      className="min-w-0 flex-1 truncate text-left text-accent hover:underline"
                    >
                      {label}
                    </button>
                  ) : (
                    <span className="min-w-0 flex-1 truncate">{label}</span>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={i === 0}
                    aria-label={t("admin.processDesigner.panelActivity.moveUp", { name: label })}
                    onClick={() => setIds((cur) => moveItem(cur, i, -1))}
                    data-testid={`activity-panel-up-${id}`}
                  >
                    ↑
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={i === ids.length - 1}
                    aria-label={t("admin.processDesigner.panelActivity.moveDown", { name: label })}
                    onClick={() => setIds((cur) => moveItem(cur, i, 1))}
                    data-testid={`activity-panel-down-${id}`}
                  >
                    ↓
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={t("admin.processDesigner.panelActivity.remove", { name: label })}
                    onClick={() => setIds((cur) => cur.filter((x) => x !== id))}
                    data-testid={`activity-panel-remove-${id}`}
                  >
                    ×
                  </Button>
                </li>
              );
            })}
          </ol>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {addable.length > 0 ? (
            <SelectField
              testId="activity-panel-add"
              aria-label={t("admin.processDesigner.panelActivity.addExisting")}
              value={null}
              placeholder={t("admin.processDesigner.panelActivity.addExisting")}
              items={addable.map((d) => ({ value: d.entity_id, label: d.name }))}
              onChange={(id) => void addExisting(id)}
            />
          ) : null}
          <Button size="sm" disabled={creating} onClick={() => void addNew()} data-testid="activity-panel-new">
            {t("admin.processDesigner.panelActivity.newForm")}
          </Button>
        </div>
      </div>
    </PanelFrame>
  );
}
