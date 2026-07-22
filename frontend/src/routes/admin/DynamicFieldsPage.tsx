import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  ApiError,
  type DynamicFieldOut,
  type DynamicFieldCreate,
  type DynamicFieldUpdate,
} from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { PlusIcon } from "@/components/ui/icons";
import {
  DynamicFieldConfigEditor,
  type DynamicFieldConfig,
} from "@/components/admin/DynamicFieldConfigEditor";
import { formatDateTime } from "@/lib/format";

const FIELD_TYPES = ["Text", "TextArea", "Checkbox", "Dropdown", "Multiselect", "Date", "DateTime"];
const OBJECT_TYPES = ["Ticket", "Article", "CustomerUser", "CustomerCompany"];

type FormState = {
  name: string;
  label: string;
  field_order: number | "";
  field_type: string;
  object_type: string;
  config: DynamicFieldConfig;
  valid_id: number;
};

function emptyForm(): FormState {
  return {
    name: "",
    label: "",
    field_order: "",
    field_type: "Text",
    object_type: "Ticket",
    config: {},
    valid_id: 1,
  };
}

function formFromRow(row: DynamicFieldOut): FormState {
  return {
    name: row.name,
    label: row.label,
    field_order: row.field_order,
    field_type: row.field_type,
    object_type: row.object_type,
    config: row.config ?? {},
    valid_id: row.valid_id,
  };
}

export function DynamicFieldsPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<DynamicFieldOut | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  const [errors, setErrors] = useState<Record<string, boolean>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["admin", "dynamic-fields"],
    queryFn: ({ signal }) => api.adminDynamicFields.list({ valid: "all", pageSize: 500 }, signal),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["admin", "dynamic-fields"] });

  const createM = useMutation({
    mutationFn: (body: DynamicFieldCreate) => api.adminDynamicFields.create(body),
    onSuccess: async () => {
      setOpen(false);
      await invalidate();
    },
  });

  const updateM = useMutation({
    mutationFn: ({ id, body }: { id: number; body: DynamicFieldUpdate }) =>
      api.adminDynamicFields.update(id, body),
    onSuccess: async () => {
      setOpen(false);
      await invalidate();
    },
  });

  const deactivateM = useMutation({
    mutationFn: (id: number) => api.adminDynamicFields.deactivate(id),
    onSuccess: () => invalidate(),
  });

  useEffect(() => {
    if (open) {
      setForm(editing ? formFromRow(editing) : emptyForm());
      setErrors({});
      setSubmitError(null);
    }
  }, [open, editing]);

  const columns: DataTableColumn<DynamicFieldOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.dynamicFields.name"), render: (r) => r.name },
    { key: "label", header: t("admin.dynamicFields.label"), render: (r) => r.label },
    { key: "field_type", header: t("admin.dynamicFields.fieldType"), render: (r) => r.field_type },
    {
      key: "object_type",
      header: t("admin.dynamicFields.objectType"),
      render: (r) => r.object_type,
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const openCreate = () => {
    setEditing(null);
    setOpen(true);
  };
  const openEdit = (row: DynamicFieldOut) => {
    setEditing(row);
    setOpen(true);
  };

  const inputClass =
    "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";
  const labelClass = "mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted";

  const validate = (): boolean => {
    const nextErrors: Record<string, boolean> = {};
    if (!form.name.trim() && !editing) nextErrors.name = true;
    if (!form.label.trim()) nextErrors.label = true;
    if (form.field_order === "") nextErrors.field_order = true;
    if (
      (form.field_type === "Dropdown" || form.field_type === "Multiselect") &&
      Object.keys((form.config.PossibleValues as Record<string, string>) ?? {}).length === 0
    ) {
      nextErrors.config = true;
    }
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate()) return;
    setSubmitError(null);
    try {
      if (editing) {
        await updateM.mutateAsync({
          id: editing.id,
          body: {
            label: form.label,
            field_order: Number(form.field_order),
            config: form.config,
            valid_id: form.valid_id,
          },
        });
      } else {
        await createM.mutateAsync({
          name: form.name,
          label: form.label,
          field_order: Number(form.field_order),
          field_type: form.field_type,
          object_type: form.object_type as DynamicFieldCreate["object_type"],
          config: form.config,
          valid_id: form.valid_id,
        });
      }
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
    }
  };

  return (
    <div className="space-y-3 p-4" data-testid="admin-dynamic-fields-page">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.dynamicFields.title_plural")}
        </h1>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-new-button"
          onClick={openCreate}
          aria-label={t("admin.dynamicFields.new")}
          title={t("admin.dynamicFields.new")}
          className="!px-2"
        >
          <PlusIcon className="text-[16px]" />
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={listQ.data?.items ?? []}
        rowKey={(r) => r.id}
        isLoading={listQ.isLoading}
        isRowValid={(r) => r.valid_id === 1}
        onEdit={openEdit}
        onDeactivate={(r) => deactivateM.mutate(r.id)}
        testId="admin-dynamic-fields-table"
      />

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? t("admin.form.editTitle", { title: t("admin.dynamicFields.title_plural") }) : t("admin.dynamicFields.new")}
        className="max-w-lg"
      >
        <form
          data-testid="dynamic-field-form"
          onSubmit={(e) => {
            e.preventDefault();
            void handleSubmit();
          }}
          className="flex max-h-[70vh] flex-col gap-3 overflow-y-auto"
        >
          <div>
            <span className={labelClass}>
              {t("admin.dynamicFields.name")}
              {!editing && <span className="text-escalation"> *</span>}
              <HelpPopover
                title={t("admin.dynamicFields.name")}
                testId="dynamic-field-help-name"
              >
                {t("admin.help.dynamicFields.name")}
              </HelpPopover>
            </span>
            {editing ? (
              <p className="text-sm text-ink">{form.name}</p>
            ) : (
              <input
                data-testid="dynamic-field-name"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                className={`${inputClass} ${errors.name ? "border-escalation" : ""}`}
              />
            )}
          </div>

          <div>
            <span className={labelClass}>
              {t("admin.dynamicFields.label")}
              <span className="text-escalation"> *</span>
            </span>
            <input
              data-testid="dynamic-field-label"
              value={form.label}
              onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
              className={`${inputClass} ${errors.label ? "border-escalation" : ""}`}
            />
          </div>

          <div>
            <span className={labelClass}>
              {t("admin.dynamicFields.fieldOrder")}
              <span className="text-escalation"> *</span>
              <HelpPopover
                title={t("admin.dynamicFields.fieldOrder")}
                testId="dynamic-field-help-field-order"
              >
                {t("admin.help.dynamicFields.fieldOrder")}
              </HelpPopover>
            </span>
            <input
              data-testid="dynamic-field-order"
              type="number"
              value={form.field_order}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  field_order: e.target.value === "" ? "" : Number(e.target.value),
                }))
              }
              className={`${inputClass} ${errors.field_order ? "border-escalation" : ""}`}
            />
          </div>

          <div>
            <span className={labelClass}>
              {t("admin.dynamicFields.fieldType")}
              <HelpPopover
                title={t("admin.dynamicFields.fieldType")}
                testId="dynamic-field-help-field-type"
              >
                {t("admin.help.dynamicFields.fieldType")}
              </HelpPopover>
            </span>
            {editing ? (
              <p className="text-sm text-ink">{form.field_type}</p>
            ) : (
              <select
                data-testid="dynamic-field-type"
                value={form.field_type}
                onChange={(e) =>
                  setForm((f) => ({ ...f, field_type: e.target.value, config: {} }))
                }
                className={inputClass}
              >
                {FIELD_TYPES.map((ft) => (
                  <option key={ft} value={ft}>
                    {ft}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div>
            <span className={labelClass}>
              {t("admin.dynamicFields.objectType")}
              <HelpPopover
                title={t("admin.dynamicFields.objectType")}
                testId="dynamic-field-help-object-type"
              >
                {t("admin.help.dynamicFields.objectType")}
              </HelpPopover>
            </span>
            {editing ? (
              <p className="text-sm text-ink">{form.object_type}</p>
            ) : (
              <select
                data-testid="dynamic-field-object-type"
                value={form.object_type}
                onChange={(e) => setForm((f) => ({ ...f, object_type: e.target.value }))}
                className={inputClass}
              >
                {OBJECT_TYPES.map((ot) => (
                  <option key={ot} value={ot}>
                    {ot}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div>
            <span className={labelClass}>
              {t("admin.dynamicFields.config")}
              <HelpPopover
                title={t("admin.dynamicFields.config")}
                testId="dynamic-field-help-config"
              >
                {t("admin.help.dynamicFields.config")}
              </HelpPopover>
            </span>
            <DynamicFieldConfigEditor
              fieldType={form.field_type}
              value={form.config}
              onChange={(config) => setForm((f) => ({ ...f, config }))}
            />
          </div>

          <div>
            <span className={labelClass}>{t("admin.table.status")}</span>
            <select
              data-testid="dynamic-field-valid"
              value={form.valid_id}
              onChange={(e) => setForm((f) => ({ ...f, valid_id: Number(e.target.value) }))}
              className={inputClass}
            >
              <option value={1}>{t("admin.table.valid")}</option>
              <option value={2}>{t("admin.table.invalid")}</option>
            </select>
          </div>

          {submitError && (
            <p className="text-sm text-escalation" data-testid="dynamic-field-form-error">
              {submitError}
            </p>
          )}

          <div className="flex justify-end gap-2 border-t border-hairline pt-3">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              {t("admin.form.cancel")}
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={createM.isPending || updateM.isPending}
              data-testid="dynamic-field-form-submit"
            >
              {createM.isPending || updateM.isPending
                ? t("admin.form.saving")
                : t("admin.form.save")}
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
