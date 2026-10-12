import { useTranslation } from "react-i18next";
import { SelectField } from "@/components/ui/SelectField";
import type { DesignerEntity } from "@/lib/processDesignerApi";
import { setStart, type ProcessShape } from "../canvasModel";
import { dialogIdsOf } from "./activityModel";
import { PanelFrame } from "./PanelFrame";

/** Znuny's process states (`pm_process.state_entity_id`). */
export const PROCESS_STATES = [
  { id: "S1", key: "Active" },
  { id: "S2", key: "Inactive" },
  { id: "S3", key: "FadeAway" },
] as const;

export type ProcessStateId = (typeof PROCESS_STATES)[number]["id"];

/** Process fields besides the shape; saved together with the canvas. */
export type ProcessFields = { name: string; description: string; stateEntityId: ProcessStateId };

const inputCls = "w-full rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink";

/**
 * Process-level properties. Edits only the local fields/shape; the page's "Speichern" persists them
 * (same draft semantics as the canvas).
 */
export function ProcessPanel({
  fields,
  shape,
  activities,
  dialogs,
  onFieldsChange,
  onShapeChange,
}: {
  fields: ProcessFields;
  shape: ProcessShape;
  activities: DesignerEntity[];
  dialogs: DesignerEntity[];
  onFieldsChange: (next: ProcessFields) => void;
  onShapeChange: (next: ProcessShape) => void;
}) {
  const { t } = useTranslation();
  const byId = new Map(activities.map((a) => [a.entity_id, a]));
  const dialogName = new Map(dialogs.map((d) => [d.entity_id, d.name]));
  const stepItems = Object.keys(shape.path).map((id) => ({ value: id, label: byId.get(id)?.name ?? id }));
  const formItems = dialogIdsOf(byId.get(shape.startActivity)?.config ?? {}).map((id) => ({
    value: id,
    label: dialogName.get(id) ?? id,
  }));

  return (
    <PanelFrame title={t("admin.processDesigner.panelProcess.title")} testId="process-panel">
      <label className="block space-y-1 text-xs text-muted">
        {t("admin.processDesigner.panelProcess.name")}
        <input
          value={fields.name}
          onChange={(e) => onFieldsChange({ ...fields, name: e.target.value })}
          className={inputCls}
          data-testid="process-panel-name"
        />
      </label>
      <label className="block space-y-1 text-xs text-muted">
        {t("admin.processDesigner.panelProcess.description")}
        <textarea
          rows={3}
          value={fields.description}
          onChange={(e) => onFieldsChange({ ...fields, description: e.target.value })}
          className={inputCls}
          data-testid="process-panel-description"
        />
      </label>
      <div className="space-y-1">
        <span className="text-xs text-muted">{t("admin.processes.stateLabel")}</span>
        <SelectField
          testId="process-panel-state"
          aria-label={t("admin.processes.stateLabel")}
          value={fields.stateEntityId}
          items={PROCESS_STATES.map((s) => ({ value: s.id, label: t(`admin.processes.states.${s.key}`) }))}
          onChange={(v) => onFieldsChange({ ...fields, stateEntityId: v })}
        />
      </div>
      <div className="space-y-1">
        <span className="text-xs text-muted">{t("admin.processDesigner.panelProcess.startStep")}</span>
        <SelectField
          testId="process-panel-start-step"
          aria-label={t("admin.processDesigner.panelProcess.startStep")}
          value={shape.startActivity || null}
          items={stepItems}
          placeholder={t("admin.processDesigner.panelProcess.none")}
          onChange={(id) => {
            const first = dialogIdsOf(byId.get(id)?.config ?? {})[0] ?? "";
            onShapeChange(setStart(shape, id, first));
          }}
        />
      </div>
      <div className="space-y-1">
        <span className="text-xs text-muted">{t("admin.processDesigner.panelProcess.startForm")}</span>
        <SelectField
          testId="process-panel-start-form"
          aria-label={t("admin.processDesigner.panelProcess.startForm")}
          value={shape.startActivityDialog || null}
          items={formItems}
          placeholder={t("admin.processDesigner.panelProcess.none")}
          disabled={!shape.startActivity}
          onChange={(id) => onShapeChange(setStart(shape, shape.startActivity, id))}
        />
      </div>
    </PanelFrame>
  );
}
