import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "@tanstack/react-router";
import { ApiError } from "@/lib/api";
import {
  processDesignerApi,
  type DesignerEntity,
  type EntitySummary,
  type ProcessDraft,
  type SubEntityType,
  type ValidationIssue,
} from "@/lib/processDesignerApi";
import { apiErrorMessage } from "@/lib/avatarApi";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { DesignerCanvas, type Selection } from "@/components/process-designer/DesignerCanvas";
import { EntityPalette } from "@/components/process-designer/EntityPalette";
import { isDirty, shapeOf, type ProcessShape } from "@/components/process-designer/canvasModel";
import { ProcessPanel, type ProcessFields, type ProcessStateId } from "@/components/process-designer/panels/ProcessPanel";
import { ActivityPanel } from "@/components/process-designer/panels/ActivityPanel";
import { NEW_DIALOG_CONFIG } from "@/components/process-designer/panels/activityModel";
import { TransitionPanel } from "@/components/process-designer/panels/TransitionPanel";
import { TransitionActions } from "@/components/process-designer/panels/TransitionActions";
import { DialogPanel } from "@/components/process-designer/panels/DialogPanel";
import { ActionPanel } from "@/components/process-designer/panels/ActionPanel";
import { MODULE_PREFIX } from "@/components/process-designer/panels/actionForm";
import { setActions } from "@/components/process-designer/canvasModel";
import { ValidationPanel } from "@/components/process-designer/ValidationPanel";
import { RevisionsPanel } from "@/components/process-designer/RevisionsPanel";
import { DeployBar } from "@/components/process-designer/DeployBar";
import { targetOfIssue } from "@/components/process-designer/validationSelect";
import { usePhoneWidth } from "@/components/process-designer/usePhoneWidth";
import {
  ENTITY_LIBRARY_KEY,
  PROCESS_LIST_KEY,
  draftHas,
  mergeEntity,
  otherUsers,
  useEntityLibrary,
} from "@/components/process-designer/entityLibrary";

const STATE_TONE: Record<string, "success" | "muted" | "warn"> = {
  Active: "success",
  Inactive: "muted",
  FadeAway: "warn",
};

/** "Also used by …": an edit of a shared step, form, transition or action changes them as well. */
function SharedNote({ names, testId }: { names: string[]; testId: string }) {
  const { t } = useTranslation();
  if (names.length === 0) return null;
  return (
    <p className="rounded-md border border-amber bg-surface px-2 py-1 text-xs text-amber" data-testid={testId}>
      {t("admin.processDesigner.sharedWith", { names: names.join(", ") })}
    </p>
  );
}

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
  const validationKey = useMemo(() => ["admin", "process-designer", "validation", processEntityId], [processEntityId]);
  const validationQ = useQuery({
    queryKey: validationKey,
    queryFn: ({ signal }) => processDesignerApi.validation(processEntityId, signal),
  });
  const issues = validationQ.data;
  // Which drawer is open below the editor: the check result or the deployed versions.
  const [drawer, setDrawer] = useState<"validation" | "revisions" | null>(null);
  const refreshValidation = useCallback(() => void qc.invalidateQueries({ queryKey: validationKey }), [qc, validationKey]);
  const draftKey = useMemo(() => ["admin", "process-designer", "draft", processEntityId], [processEntityId]);
  const draft = draftQ.data;
  // Shared entities of all processes ("Vorhandene verwenden", "used by", deleting unused ones).
  const library = useEntityLibrary();
  const refreshLibrary = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ENTITY_LIBRARY_KEY });
    void qc.invalidateQueries({ queryKey: PROCESS_LIST_KEY });
  }, [qc]);
  const activityNames = useMemo(() => new Map(library.Activity.map((a) => [a.entity_id, a.name])), [library.Activity]);
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [libraryNotice, setLibraryNotice] = useState<string | null>(null);

  // Canvas edits (layout, path, start) stay local until "Speichern", like Znuny's canvas.
  const [edited, setEdited] = useState<ProcessShape | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [saveError, setSaveError] = useState(false);

  const stored = useMemo(() => (draft ? shapeOf(draft) : null), [draft]);
  const shape = edited ?? stored;
  const storedFields = useMemo<ProcessFields | null>(
    () =>
      draft
        ? {
            name: draft.process.name,
            description: (draft.process.config as { Description?: string }).Description ?? "",
            stateEntityId: draft.state_entity_id as ProcessStateId,
          }
        : null,
    [draft],
  );
  const [editedFields, setEditedFields] = useState<ProcessFields | null>(null);
  const fields = editedFields ?? storedFields;
  const fieldsDirty = !!(
    editedFields &&
    storedFields &&
    (editedFields.name !== storedFields.name ||
      editedFields.description !== storedFields.description ||
      editedFields.stateEntityId !== storedFields.stateEntityId)
  );
  const dirty = fieldsDirty || !!(edited && stored && isDirty(edited, stored));

  const discard = useCallback(() => {
    setEdited(null);
    setEditedFields(null);
    setSelection(null);
    setConflict(false);
    setSaveError(false);
  }, []);

  const save = useCallback(async () => {
    if (!draft || !shape || !fields || !dirty || saving) return;
    setSaving(true);
    setSaveError(false);
    try {
      const next = await processDesignerApi.updateProcess(processEntityId, {
        expected_version: draft.process.version,
        name: fields.name.trim() || draft.process.name,
        description: fields.description,
        state_entity_id: fields.stateEntityId,
        path: shape.path,
        layout: shape.layout,
        start_activity: shape.startActivity,
        start_activity_dialog: shape.startActivityDialog,
      });
      qc.setQueryData(draftKey, next);
      refreshValidation();
      refreshLibrary();
      setEdited(null);
      setEditedFields(null);
      setConflict(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setConflict(true);
      else setSaveError(true);
    } finally {
      setSaving(false);
    }
  }, [draft, shape, fields, dirty, saving, processEntityId, qc, draftKey, refreshValidation, refreshLibrary]);

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
    async (type: "Activity" | "ActivityDialog" | "Transition" | "TransitionAction", name: string, config: Record<string, unknown>) => {
      const created: DesignerEntity = await processDesignerApi.createEntity(type, { name, config });
      qc.setQueryData<ProcessDraft>(draftKey, (cur) => (cur ? mergeEntity(cur, created) : cur));
      refreshLibrary();
      return created.entity_id;
    },
    [qc, draftKey, refreshLibrary],
  );
  // An existing entity of another process: load it into the cached draft before it is used, so the
  // canvas and the panels know its name and configuration.
  const adoptEntity = useCallback(
    async (type: SubEntityType, entityId: string) => {
      const cur = qc.getQueryData<ProcessDraft>(draftKey);
      if (cur && draftHas(cur, type, entityId)) return;
      const entity = await processDesignerApi.getEntity(type, entityId);
      qc.setQueryData<ProcessDraft>(draftKey, (c) => (c ? mergeEntity(c, entity) : c));
      if (type !== "Activity") return;
      // A step brings its forms along, so they open before the process is saved.
      const forms = Object.values((entity.config?.ActivityDialog ?? {}) as Record<string, unknown>).filter(
        (x): x is string => typeof x === "string",
      );
      await Promise.all(
        forms.map(async (formId) => {
          const now = qc.getQueryData<ProcessDraft>(draftKey);
          if (!now || draftHas(now, "ActivityDialog", formId)) return;
          const form = await processDesignerApi.getEntity("ActivityDialog", formId);
          qc.setQueryData<ProcessDraft>(draftKey, (c) => (c ? mergeEntity(c, form) : c));
        }),
      );
    },
    [qc, draftKey],
  );
  const deleteEntity = useCallback(
    async (entity: EntitySummary) => {
      const ok = await confirm({
        title: t("admin.processDesigner.delete"),
        message: t("admin.processDesigner.deleteEntityConfirm", { name: entity.name }),
        confirmLabel: t("admin.processDesigner.delete"),
        variant: "danger",
      });
      if (!ok) return;
      setLibraryNotice(null);
      const type = entity.entity_type as SubEntityType;
      try {
        await processDesignerApi.deleteEntity(type, entity.entity_id);
      } catch (err) {
        let text = apiErrorMessage(err, t("admin.processDesigner.deleteEntityError"));
        if (err instanceof ApiError && err.status === 409) {
          // Meanwhile somebody started using it: name the users from a fresh list.
          const fresh = await processDesignerApi.listEntities(type).catch(() => []);
          const users = fresh.find((e) => e.entity_id === entity.entity_id)?.used_by ?? [];
          const names = type === "ActivityDialog" ? activityNames : library.processNames;
          if (users.length > 0)
            text = t("admin.processDesigner.inUse", { names: users.map((id) => names.get(id) ?? id).join(", ") });
        }
        setLibraryNotice(text);
      } finally {
        refreshLibrary();
      }
    },
    [confirm, t, refreshLibrary, activityNames, library.processNames],
  );
  const createActivity = useCallback(
    () => createEntity("Activity", t("admin.processDesigner.newStep"), { ActivityDialog: {} }),
    [createEntity, t],
  );
  const createDialog = useCallback(
    () => createEntity("ActivityDialog", t("admin.processDesigner.newForm"), { ...NEW_DIALOG_CONFIG }),
    [createEntity, t],
  );
  const createTransition = useCallback(
    (name: string) => createEntity("Transition", name, { ConditionLinking: "and", Condition: {} }),
    [createEntity],
  );

  const createAction = useCallback(
    () => createEntity("TransitionAction", t("admin.processDesigner.newAction"), { Module: MODULE_PREFIX + "TicketStateSet", Config: {} }),
    [createEntity, t],
  );
  const [actionId, setActionId] = useState<string | null>(null);
  // The form whose panel is open below the step panel (its own entity, saved on its own).
  const [dialogId, setDialogId] = useState<string | null>(null);
  const selectedDialog = useMemo(
    () => (draft && dialogId ? draft.activity_dialogs.find((d) => d.entity_id === dialogId) : undefined),
    [draft, dialogId],
  );
  const onDialogSaved = useCallback(
    (saved: DesignerEntity) => {
      qc.setQueryData<ProcessDraft>(draftKey, (cur) =>
        cur
          ? { ...cur, activity_dialogs: cur.activity_dialogs.map((x) => (x.entity_id === saved.entity_id ? saved : x)) }
          : cur,
      );
      refreshValidation();
    },
    [qc, draftKey, refreshValidation],
  );
  const selectedTransition = useMemo(
    () => (draft && selection?.kind === "transition" ? draft.transitions.find((x) => x.entity_id === selection.transitionId) : undefined),
    [draft, selection],
  );
  const transitionActionIds = useMemo(() => {
    if (!shape || selection?.kind !== "transition") return [];
    const a = shape.path[selection.from]?.[selection.transitionId]?.TransitionAction;
    return Array.isArray(a) ? a.filter((x): x is string => typeof x === "string") : [];
  }, [shape, selection]);
  const selectedAction = useMemo(
    () => (draft && actionId ? draft.transition_actions.find((a) => a.entity_id === actionId) : undefined),
    [draft, actionId],
  );
  const onTransitionSaved = useCallback(
    (saved: DesignerEntity) => {
      qc.setQueryData<ProcessDraft>(draftKey, (cur) =>
        cur ? { ...cur, transitions: cur.transitions.map((x) => (x.entity_id === saved.entity_id ? saved : x)) } : cur,
      );
      refreshValidation();
    },
    [qc, draftKey, refreshValidation],
  );
  const onActionSaved = useCallback(
    (saved: DesignerEntity) => {
      qc.setQueryData<ProcessDraft>(draftKey, (cur) =>
        cur
          ? { ...cur, transition_actions: cur.transition_actions.map((x) => (x.entity_id === saved.entity_id ? saved : x)) }
          : cur,
      );
      refreshValidation();
    },
    [qc, draftKey, refreshValidation],
  );

  const selectedActivity = useMemo(
    () => (draft && selection?.kind === "activity" ? draft.activities.find((a) => a.entity_id === selection.id) : undefined),
    [draft, selection],
  );
  const onActivitySaved = useCallback(
    (saved: DesignerEntity) => {
      qc.setQueryData<ProcessDraft>(draftKey, (cur) =>
        cur ? { ...cur, activities: cur.activities.map((a) => (a.entity_id === saved.entity_id ? saved : a)) } : cur,
      );
      refreshValidation();
    },
    [qc, draftKey, refreshValidation],
  );

  const selectIssue = useCallback(
    (issue: ValidationIssue) => {
      if (!draft || !shape) return;
      const target = targetOfIssue(issue, draft, shape);
      setSelection(target.selection);
      setActionId(target.actionId);
    },
    [draft, shape],
  );
  const onRestored = useCallback(
    (restored: ProcessDraft) => {
      qc.setQueryData(draftKey, restored);
      discard();
      refreshValidation();
      refreshLibrary();
      void qc.invalidateQueries({ queryKey: ["admin", "deploy-status"] });
    },
    [qc, draftKey, discard, refreshValidation, refreshLibrary],
  );

  // Everything the local (possibly unsaved) shape references: such entities are not offered for
  // deletion, and a form shared only with steps of this process is not "shared".
  const referenced = useMemo(() => {
    const ids = new Set<string>();
    if (!shape || !draft) return ids;
    ids.add(shape.startActivity);
    ids.add(shape.startActivityDialog);
    for (const [aid, transitions] of Object.entries(shape.path)) {
      ids.add(aid);
      for (const [tid, entry] of Object.entries(transitions as Record<string, Record<string, unknown>>)) {
        ids.add(tid);
        ids.add(String(entry.ActivityEntityID ?? ""));
        const actions = entry.TransitionAction;
        for (const a of Array.isArray(actions) ? actions : []) ids.add(String(a));
      }
    }
    for (const a of draft.activities) {
      if (!ids.has(a.entity_id)) continue;
      const dialogs = (a.config as { ActivityDialog?: Record<string, unknown> }).ActivityDialog ?? {};
      for (const d of Object.values(dialogs)) ids.add(String(d));
    }
    return ids;
  }, [shape, draft]);
  const unusedEntities = useMemo(
    () =>
      [...library.Activity, ...library.ActivityDialog, ...library.Transition, ...library.TransitionAction].filter(
        (e) => e.used_by.length === 0 && !referenced.has(e.entity_id),
      ),
    [library, referenced],
  );
  const ownProcess = useMemo(() => new Set([processEntityId]), [processEntityId]);
  const sharedNames = (type: SubEntityType, entityId: string | undefined) =>
    entityId
      ? type === "ActivityDialog"
        ? otherUsers(library.ActivityDialog.find((e) => e.entity_id === entityId), referenced, activityNames)
        : otherUsers(library[type].find((e) => e.entity_id === entityId), ownProcess, library.processNames)
      : [];

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
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={STATE_TONE[draft.state_name] ?? "muted"} data-testid="process-designer-state">
                {draft.state_name
                  ? t(`admin.processes.states.${draft.state_name}`, { defaultValue: draft.state_name })
                  : "—"}
              </Badge>
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
                  <Button
                    size="sm"
                    onClick={() => {
                      setDrawer("validation");
                      void validationQ.refetch();
                    }}
                    disabled={validationQ.isFetching}
                    data-testid="process-designer-check"
                  >
                    {validationQ.isFetching ? t("admin.processDesigner.checking") : t("admin.processDesigner.check")}
                    {issues && issues.length > 0 ? ` (${issues.length})` : ""}
                  </Button>
                  <a
                    href={processDesignerApi.exportUrl(processEntityId)}
                    download
                    data-testid="process-designer-export"
                    className="rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-ink hover:bg-surface-subtle"
                  >
                    {t("admin.processes.export")}
                  </a>
                  <Button
                    size="sm"
                    onClick={() => setDrawer(drawer === "revisions" ? null : "revisions")}
                    data-testid="process-designer-versions"
                  >
                    {t("admin.processDesigner.versions")}
                  </Button>
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

          {!phone ? (
            <DeployBar
              processEntityId={processEntityId}
              disabled={dirty}
              onValidated={(fresh) => qc.setQueryData(validationKey, fresh)}
            />
          ) : null}

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
          {libraryNotice ? (
            <p role="alert" className="text-sm text-danger" data-testid="process-designer-library-error">
              {libraryNotice}
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
                  activities={library.Activity}
                  usedIds={new Set(Object.keys(shape?.path ?? {}))}
                  unused={unusedEntities}
                  onDelete={(e) => void deleteEntity(e)}
                />
              </aside>
              <section className="min-h-0 rounded-lg border border-hairline bg-surface">
                {shape ? (
                  <DesignerCanvas
                    draft={draft}
                    shape={shape}
                    selection={selection}
                    onSelect={(sel) => {
                      setSelection(sel);
                      setActionId(null);
                      setDialogId(null);
                    }}
                    onShapeChange={setEdited}
                    createActivity={createActivity}
                    createTransition={createTransition}
                    transitions={library.Transition}
                    adoptEntity={adoptEntity}
                    issues={issues}
                  />
                ) : null}
              </section>
              <aside className="rounded-lg border border-hairline bg-surface p-3" data-testid="process-designer-panel">
                <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
                  {t("admin.processDesigner.panel")}
                </h2>
                <div className="mt-2">
                  {selectedActivity ? (
                    <div className="space-y-4">
                      <SharedNote names={sharedNames("Activity", selectedActivity.entity_id)} testId="shared-activity" />
                      <ActivityPanel
                        key={`${selectedActivity.entity_id}:${selectedActivity.version}`}
                        activity={selectedActivity}
                        dialogs={draft.activity_dialogs}
                        library={library.ActivityDialog}
                        adoptDialog={(id) => adoptEntity("ActivityDialog", id)}
                        createDialog={createDialog}
                        onSaved={onActivitySaved}
                        onReload={() => void draftQ.refetch()}
                        onOpenForm={setDialogId}
                        openFormId={dialogId}
                      />
                      {selectedDialog ? (
                        <SharedNote names={sharedNames("ActivityDialog", selectedDialog.entity_id)} testId="shared-dialog" />
                      ) : null}
                      {selectedDialog && catalogQ.data ? (
                        <DialogPanel
                          key={`${selectedDialog.entity_id}:${selectedDialog.version}`}
                          dialog={selectedDialog}
                          catalog={catalogQ.data}
                          onSaved={onDialogSaved}
                          onReload={() => void draftQ.refetch()}
                        />
                      ) : null}
                    </div>
                  ) : selectedTransition && shape && catalogQ.data && selection?.kind === "transition" ? (
                    <div className="space-y-4">
                      <SharedNote names={sharedNames("Transition", selectedTransition.entity_id)} testId="shared-transition" />
                      <TransitionPanel
                        key={`${selectedTransition.entity_id}:${selectedTransition.version}`}
                        transition={selectedTransition}
                        catalog={catalogQ.data}
                        onSaved={onTransitionSaved}
                        onReload={() => void draftQ.refetch()}
                      />
                      <TransitionActions
                        actionIds={transitionActionIds}
                        actions={draft.transition_actions}
                        library={library.TransitionAction}
                        adoptAction={(id) => adoptEntity("TransitionAction", id)}
                        selectedId={actionId}
                        createAction={createAction}
                        onChange={(ids) => setEdited(setActions(shape, selection.from, selection.transitionId, ids))}
                        onSelect={setActionId}
                      />
                      {selectedAction && transitionActionIds.includes(selectedAction.entity_id) ? (
                        <SharedNote names={sharedNames("TransitionAction", selectedAction.entity_id)} testId="shared-action" />
                      ) : null}
                      {selectedAction && transitionActionIds.includes(selectedAction.entity_id) ? (
                        <ActionPanel
                          key={`${selectedAction.entity_id}:${selectedAction.version}`}
                          action={selectedAction}
                          catalog={catalogQ.data}
                          onSaved={onActionSaved}
                          onReload={() => void draftQ.refetch()}
                        />
                      ) : null}
                    </div>
                  ) : selection ? (
                    <p className="text-sm text-muted" data-testid="process-designer-selection">
                      {t("admin.processDesigner.selected", { name: selectedName })}
                    </p>
                  ) : shape && fields ? (
                    <ProcessPanel
                      fields={fields}
                      shape={shape}
                      activities={draft.activities}
                      dialogs={draft.activity_dialogs}
                      onFieldsChange={setEditedFields}
                      onShapeChange={setEdited}
                    />
                  ) : null}
                </div>
              </aside>
            </div>
          )}
          {!phone && drawer ? (
            <section
              className="max-h-80 overflow-auto rounded-lg border border-hairline bg-surface p-3"
              data-testid="process-designer-drawer"
            >
              {drawer === "validation" ? (
                <ValidationPanel
                  issues={issues}
                  loading={validationQ.isFetching}
                  error={validationQ.isError}
                  onSelect={selectIssue}
                  onRecheck={() => void validationQ.refetch()}
                />
              ) : (
                <RevisionsPanel processEntityId={processEntityId} onRestored={onRestored} />
              )}
            </section>
          ) : null}
        </>
      )}
      {confirmDialog}
    </div>
  );
}
