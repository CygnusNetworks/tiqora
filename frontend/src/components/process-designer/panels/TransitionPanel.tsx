import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { processDesignerApi, type DesignerCatalog, type DesignerEntity } from "@/lib/processDesignerApi";
import { ConditionBuilder } from "./ConditionBuilder";
import { buildTransitionConfig, hasBlockingProblem, readCondition, type ConditionModel } from "./conditionModel";
import { PanelFrame } from "./PanelFrame";

const inputCls = "w-full rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink";

/**
 * Transition properties: name and condition. Saves itself (`PUT /entities/Transition/{eid}`);
 * keys of the stored config the builder does not know are preserved.
 */
export function TransitionPanel({
  transition,
  catalog,
  onSaved,
  onReload,
}: {
  transition: DesignerEntity;
  catalog: DesignerCatalog;
  onSaved: (saved: DesignerEntity) => void;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  const stored = useMemo(() => readCondition(transition.config), [transition.config]);
  const [name, setName] = useState(transition.name);
  const [model, setModel] = useState<ConditionModel>(stored);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState(false);

  const dirty = name !== transition.name || JSON.stringify(model) !== JSON.stringify(stored);
  const blocked = hasBlockingProblem(model);

  const save = async () => {
    setSaving(true);
    setError(false);
    try {
      const saved = await processDesignerApi.updateEntity("Transition", transition.entity_id, {
        expected_version: transition.version,
        name: name.trim() || transition.name,
        config: buildTransitionConfig(transition.config, model),
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
  const discard = () => {
    setName(transition.name);
    setModel(stored);
  };

  return (
    <PanelFrame
      title={t("admin.processDesigner.condition.title")}
      testId="transition-panel"
      dirty={dirty && !blocked}
      saving={saving}
      conflict={conflict}
      error={error}
      onSave={() => void save()}
      onDiscard={discard}
      onReload={onReload}
    >
      <label className="block space-y-1 text-xs text-muted">
        {t("admin.processDesigner.condition.name")}
        <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} data-testid="transition-panel-name" />
      </label>
      <p className="text-xs text-muted" data-testid="transition-panel-note">
        {t("admin.processDesigner.condition.note")}
      </p>
      <ConditionBuilder model={model} catalog={catalog} onChange={setModel} />
    </PanelFrame>
  );
}
