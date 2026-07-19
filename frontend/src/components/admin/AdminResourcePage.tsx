import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "./DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "./CrudDrawer";

export type AdminCrudApi<Out, Create, Update> = {
  list: (signal?: AbortSignal) => Promise<Out[]>;
  create: (body: Create, signal?: AbortSignal) => Promise<Out>;
  update: (id: number | string, body: Update, signal?: AbortSignal) => Promise<Out>;
  deactivate: (id: number | string, signal?: AbortSignal) => Promise<void>;
};

export type AdminResourcePageProps<Out, Create, Update> = {
  resourceKey: string;
  title: string;
  newLabel: string;
  api: AdminCrudApi<Out, Create, Update>;
  idOf: (row: Out) => string | number;
  columns: DataTableColumn<Out>[];
  fields: FieldDef[];
  toFormValues: (row: Out | null) => FieldValues;
  toCreateBody: (values: FieldValues) => Create;
  toUpdateBody: (values: FieldValues) => Update;
  isRowValid?: (row: Out) => boolean;
};

const defaultIsRowValid = (row: unknown): boolean =>
  (row as { valid_id?: number }).valid_id === undefined ||
  (row as { valid_id?: number }).valid_id === 1;

/**
 * Generic list + create/edit drawer + deactivate flow, instantiated per
 * admin resource. Keeps each resource page down to column defs + field defs.
 */
export function AdminResourcePage<Out, Create, Update>({
  resourceKey,
  title,
  newLabel,
  api,
  idOf,
  columns,
  fields,
  toFormValues,
  toCreateBody,
  toUpdateBody,
  isRowValid,
}: AdminResourcePageProps<Out, Create, Update>) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Out | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["admin", resourceKey],
    queryFn: ({ signal }) => api.list(signal),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin", resourceKey] });

  const createM = useMutation({
    mutationFn: (values: FieldValues) => api.create(toCreateBody(values)),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const updateM = useMutation({
    mutationFn: ({ id, values }: { id: string | number; values: FieldValues }) =>
      api.update(id, toUpdateBody(values)),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const deactivateM = useMutation({
    mutationFn: (id: string | number) => api.deactivate(id),
    onSuccess: () => invalidate(),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };

  const openEdit = (row: Out) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    try {
      if (editing) {
        await updateM.mutateAsync({ id: idOf(editing), values });
      } else {
        await createM.mutateAsync(values);
      }
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      throw err;
    }
  };

  return (
    <div className="space-y-3 p-4" data-testid={`admin-${resourceKey}-page`}>
      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">{title}</h1>
        <Button variant="primary" size="sm" data-testid="admin-new-button" onClick={openCreate}>
          {newLabel}
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={listQ.data ?? []}
        rowKey={idOf}
        isLoading={listQ.isLoading}
        isRowValid={isRowValid ?? defaultIsRowValid}
        onEdit={openEdit}
        onDeactivate={(row) => deactivateM.mutate(idOf(row))}
        testId={`admin-${resourceKey}-table`}
      />
      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={editing ? t("admin.form.editTitle", { title }) : newLabel}
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={toFormValues(editing)}
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-form"
      />
    </div>
  );
}
