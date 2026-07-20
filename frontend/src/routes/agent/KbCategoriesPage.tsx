import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  ApiError,
  type CategoryOut,
  type CategoryIn,
  type CategoryUpdateIn,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { CategoryTree } from "@/components/agent/CategoryTree";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";

/** Sentinel select value for "no parent" (0 is not a valid category id). */
const NO_PARENT = 0;

/** Read a FieldValues entry as a number[] (the group multi-select value). */
function asNumberArray(value: unknown): number[] {
  return Array.isArray(value) ? value.filter((v): v is number => typeof v === "number") : [];
}

/**
 * Agent-area KB category management: list/create/edit/deactivate categories.
 * Display reuses {@link CategoryTree}; the form reuses {@link CrudDrawer} with
 * a custom checkbox-list field for the permission-group multi-select.
 */
export function KbCategoriesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<CategoryOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const categoriesQ = useQuery({
    queryKey: ["kb", "categories"],
    queryFn: ({ signal }) => api.listKbCategories(signal),
  });
  const groupsQ = useQuery({
    queryKey: ["kb", "assignable-groups"],
    queryFn: ({ signal }) => api.listAssignableGroups(signal),
  });

  const categories = categoriesQ.data ?? [];
  const groups = groupsQ.data ?? [];

  const openNew = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };

  const openEdit = (id: number | null) => {
    if (id == null) return;
    const cat = categories.find((c) => c.id === id);
    if (!cat) return;
    setEditing(cat);
    setFormError(null);
    setDrawerOpen(true);
  };

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["kb", "categories"] });

  const groupField: FieldDef = {
    name: "permission_group_ids",
    label: t("kb.category.groups"),
    type: "custom",
    helpText: t("kb.category.groupsHelp"),
    render: (value, onChange) => {
      const selected = asNumberArray(value);
      if (groups.length === 0) {
        return <p className="text-sm text-muted">{t("kb.category.noGroups")}</p>;
      }
      const toggle = (gid: number, checked: boolean) =>
        onChange(checked ? [...selected, gid] : selected.filter((g) => g !== gid));
      return (
        <div
          className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded-md border border-hairline bg-surface-subtle p-2"
          data-testid="kb-category-groups"
        >
          {groups.map((g) => (
            <label key={g.id} className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid={`kb-category-group-${g.id}`}
                checked={selected.includes(g.id)}
                onChange={(e) => toggle(g.id, e.target.checked)}
                className="h-4 w-4 rounded border-hairline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              />
              {g.name}
            </label>
          ))}
        </div>
      );
    },
  };

  const parentOptions = categories
    // A category cannot be its own parent.
    .filter((c) => c.id !== editing?.id)
    .map((c) => ({ value: c.id, label: c.name }));

  const fields: FieldDef[] = [
    { name: "name", label: t("kb.category.name"), type: "text", required: true },
    {
      name: "parent_id",
      label: t("kb.category.parent"),
      type: "select",
      options: [{ value: NO_PARENT, label: t("kb.category.noParent") }, ...parentOptions],
    },
    {
      name: "slug",
      label: t("kb.category.slug"),
      type: "text",
      placeholder: t("kb.category.slugPlaceholder"),
    },
    { name: "sort", label: t("kb.category.sort"), type: "number" },
    { name: "customer_visible", label: t("kb.category.customerVisible"), type: "checkbox" },
    groupField,
    { name: "valid", label: t("kb.category.active"), type: "checkbox", hideOnCreate: true },
  ];

  const initialValues: FieldValues = editing
    ? {
        name: editing.name,
        parent_id: editing.parent_id ?? NO_PARENT,
        slug: editing.slug,
        sort: editing.sort,
        customer_visible: editing.customer_visible,
        permission_group_ids: editing.permission_group_ids ?? [],
        valid: editing.valid,
      }
    : {
        name: "",
        parent_id: NO_PARENT,
        slug: "",
        sort: 0,
        customer_visible: false,
        permission_group_ids: [],
        valid: true,
      };

  const onSubmit = async (v: FieldValues) => {
    setFormError(null);
    const parentId = Number(v.parent_id) || null;
    const slug = ((v.slug as string) || "").trim();
    const groupIds = asNumberArray(v.permission_group_ids);
    try {
      if (editing) {
        const body: CategoryUpdateIn = {
          name: v.name as string,
          parent_id: parentId,
          slug: slug || undefined,
          sort: Number(v.sort) || 0,
          customer_visible: Boolean(v.customer_visible),
          permission_group_ids: groupIds,
          valid: Boolean(v.valid),
        };
        await api.updateKbCategory(editing.id, body);
      } else {
        const body: CategoryIn = {
          name: v.name as string,
          parent_id: parentId,
          // Only send a slug the user typed; otherwise the backend derives it.
          ...(slug ? { slug } : {}),
          sort: Number(v.sort) || 0,
          customer_visible: Boolean(v.customer_visible),
          permission_group_ids: groupIds,
          valid: true,
        };
        await api.createKbCategory(body);
      }
      await invalidate();
      setDrawerOpen(false);
    } catch (err) {
      if (err instanceof ApiError) {
        // Surface KbForbidden (403) group-assignment errors verbatim.
        setFormError(err.status === 403 ? err.message : t("kb.category.saveError"));
      } else {
        throw err;
      }
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 p-3" data-testid="kb-categories-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">{t("kb.category.title")}</h1>
        <Button variant="primary" onClick={openNew} data-testid="kb-category-new">
          {t("kb.category.new")}
        </Button>
      </div>

      <div className="rounded-lg border border-hairline bg-surface p-3">
        {categoriesQ.isLoading ? (
          <div className="flex justify-center py-6">
            <Spinner />
          </div>
        ) : categories.length === 0 ? (
          <p className="px-2 py-4 text-sm text-muted">{t("kb.category.empty")}</p>
        ) : (
          <CategoryTree categories={categories} selectedId={null} onSelect={openEdit} />
        )}
      </div>

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={editing ? t("kb.category.edit") : t("kb.category.new")}
        fields={fields}
        initialValues={initialValues}
        mode={editing ? "edit" : "create"}
        onSubmit={onSubmit}
        submitError={formError}
        testIdPrefix="kb-category-form"
      />
    </div>
  );
}
