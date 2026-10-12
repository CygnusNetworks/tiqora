import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "@tanstack/react-router";
import { ApiError } from "@/lib/api";
import { processDesignerApi, type DesignerEntity, type ProcessDraft } from "@/lib/processDesignerApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { DesignerCanvas, type Selection } from "@/components/process-designer/DesignerCanvas";
import { EntityPalette } from "@/components/process-designer/EntityPalette";
import { isDirty, shapeOf, type ProcessShape } from "@/components/process-designer/canvasModel";
import { usePhoneWidth } from "@/components/process-designer/usePhoneWidth";

/**
 * Process designer (lazy route `/admin/processes/$processEntityId`). Skeleton: loads the draft and
 * the catalogue and lays out palette, canvas and side panel; the editing parts follow in later tasks.
 * On phones (< 768 px) it is a read-only list.
 */
export function ProcessDesignerPage() {
  const { t } = useTranslation();
  const { processEntityId } = useParams({ from: "/admin/processes/$processEntityId" });
  const phone = usePhoneWidth();
  const draftQ = useQuery({
    queryKey: ["admin", "process-designer", "draft", processEntityId],
    queryFn: ({ signal }) => processDesignerApi.getProcess(processEntityId, signal),
  });
  const catalogQ = useQuery({
    queryKey: ["admin", "process-designer", "catalog"],
    queryFn: ({ signal }) => processDesignerApi.catalog(signal),
  });

  const qc = useQueryClient();
  const draftKey = useMemo(() => ["admin", "process-designer", "draft", processEntityId], [processEntityId]);
  const draft = draftQ.data;

  // Canvas edits (layout, path, start) stay local until "Speichern", like Znuny's canvas.
  const [edited, setEdited] = useState<ProcessShape | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [saveError, setSaveError] = useState(false);

  const stored = useMemo(() => (draft ? shapeOf(draft) : null), [draft]);
  const shape = edited ?? stored;
  const dirty = !!(edited && stored && isDirty(edited, stored));

  const discard = useCallback(() => {
    setEdited(null);
    setSelection(null);
    setConflict(false);
    setSaveError(false);
  }, []);

  const save = useCallback(async () => {
    if (!draft || !edited || !dirty || saving) return;
    setSaving(true);
    setSaveError(false);
    try {
      const cfg = draft.process.config as { Description?: string };
      const next = await processDesignerApi.updateProcess(processEntityId, {
        expected_version: draft.process.version,
        name: draft.process.name,
        description: cfg.Description ?? "",
        state_entity_id: draft.state_entity_id as "S1" | "S2" | "S3",
        path: edited.path,
        layout: edited.layout,
        start_activity: edited.startActivity,
        start_activity_dialog: edited.startActivityDialog,
      });
      qc.setQueryData(draftKey, next);
      setEdited(null);
      setConflict(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setConflict(true);
      else setSaveError(true);
    } finally {
      setSaving(false);
    }
  }, [draft, edited, dirty, saving, processEntityId, qc, draftKey]);

  const reload = useCallback(async () => {
    discard();
    await draftQ.refetch();
  }, [discard, draftQ]);

  // Ctrl/Cmd+S saves; leaving the page with unsaved edits asks first.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);
  useEffect(() => {
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [dirty]);

  // New entities are created at once (like Znuny's popups) and added to the cached draft.
  const createEntity = useCallback(
    async (type: "Activity" | "Transition", name: string, config: Record<string, unknown>) => {
      const created: DesignerEntity = await processDesignerApi.createEntity(type, { name, config });
      qc.setQueryData<ProcessDraft>(draftKey, (cur) =>
        cur
          ? type === "Activity"
            ? { ...cur, activities: [...cur.activities, created] }
            : { ...cur, transitions: [...cur.transitions, created] }
          : cur,
      );
      return created.entity_id;
    },
    [qc, draftKey],
  );
  const createActivity = useCallback(
    () => createEntity("Activity", t("admin.processDesigner.newStep"), { ActivityDialog: {} }),
    [createEntity, t],
  );
  const createTransition = useCallback(
    (name: string) => createEntity("Transition", name, { ConditionLinking: "and", Condition: {} }),
    [createEntity],
  );

  const selectedName = useMemo(() => {
    if (!draft || !selection) return null;
    if (selection.kind === "activity")
      return draft.activities.find((a) => a.entity_id === selection.id)?.name ?? selection.id;
    return draft.transitions.find((x) => x.entity_id === selection.transitionId)?.name ?? selection.transitionId;
  }, [draft, selection]);

  return (
    <div className="flex h-full flex-col gap-3 p-4" data-testid="process-designer-page">
      <Link to="/admin/processes" className="text-sm text-accent hover:underline">
        {t("common.back")}
      </Link>

      {draftQ.isLoading || catalogQ.isLoading ? (
        <Spinner />
      ) : draftQ.isError || catalogQ.isError || !draft ? (
        <p className="text-sm text-danger">{t("admin.processDesigner.loadError")}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h1 className="font-display text-xl font-semibold text-ink">{draft.process.name}</h1>
            <div className="flex items-center gap-2">
              {draft.process.sync_state !== "synced" ? (
                <Badge tone="muted">{t("admin.processDesigner.draft")}</Badge>
              ) : null}
              {dirty ? (
                <span className="text-xs text-muted" data-testid="process-designer-dirty">
                  {t("admin.processDesigner.unsaved")}
                </span>
              ) : null}
              {!phone ? (
                <>
                  <Button size="sm" onClick={discard} disabled={!dirty || saving} data-testid="process-designer-discard">
                    {t("admin.processDesigner.discard")}
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => void save()}
                    disabled={!dirty || saving}
                    data-testid="process-designer-save"
                  >
                    {saving ? t("admin.processDesigner.saving") : t("admin.processDesigner.save")}
                  </Button>
                </>
              ) : null}
            </div>
          </div>

          {conflict ? (
            <div
              role="alert"
              data-testid="process-designer-conflict"
              className="flex items-center justify-between gap-2 rounded-lg border border-danger bg-surface px-3 py-2 text-sm text-danger"
            >
              <span>{t("admin.processDesigner.conflict")}</span>
              <Button size="sm" onClick={() => void reload()}>
                {t("admin.processDesigner.reload")}
              </Button>
            </div>
          ) : null}
          {saveError ? (
            <p role="alert" className="text-sm text-danger">
              {t("admin.processDesigner.saveError")}
            </p>
          ) : null}

          {phone ? (
            <div data-testid="process-designer-phone-list" className="space-y-2">
              <p className="text-sm text-muted">{t("admin.processDesigner.phoneReadOnly")}</p>
              <ul className="divide-y divide-hairline rounded-lg border border-hairline bg-surface">
                {draft.activities.map((a) => (
                  <li key={a.entity_id} data-testid={`process-designer-activity-${a.entity_id}`} className="px-4 py-2 text-sm text-ink">
                    {a.name}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div
              className="grid min-h-0 flex-1 grid-cols-[14rem_minmax(0,1fr)_20rem] gap-3"
              data-testid="process-designer-layout"
            >
              <aside className="rounded-lg border border-hairline bg-surface p-3" data-testid="process-designer-palette">
                <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
                  {t("admin.processDesigner.palette")}
                </h2>
                <EntityPalette
                  activities={draft.activities}
                  usedIds={new Set(Object.keys(shape?.path ?? {}))}
                />
              </aside>
              <section className="min-h-0 rounded-lg border border-hairline bg-surface">
                {shape ? (
                  <DesignerCanvas
                    draft={draft}
                    shape={shape}
                    selection={selection}
                    onSelect={setSelection}
                    onShapeChange={setEdited}
                    createActivity={createActivity}
                    createTransition={createTransition}
                  />
                ) : null}
              </section>
              <aside className="rounded-lg border border-hairline bg-surface p-3" data-testid="process-designer-panel">
                <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
                  {t("admin.processDesigner.panel")}
                </h2>
                <p className="mt-2 text-sm text-muted" data-testid="process-designer-selection">
                  {selectedName
                    ? t("admin.processDesigner.selected", { name: selectedName })
                    : t("admin.processDesigner.noSelection")}
                </p>
              </aside>
            </div>
          )}
        </>
      )}
    </div>
  );
}
