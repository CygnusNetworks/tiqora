import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import {
  api,
  ApiError,
  type PostmasterFilterOut,
  type PostmasterFilterWrite,
} from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

type MatchRow = { key: string; value: string; negate: boolean };
type SetRow = { key: string; value: string };

/** Common Match headers (Znuny AdminPostMasterFilter). Free-text still allowed. */
const MATCH_HEADER_SUGGESTIONS = [
  "From",
  "To",
  "Cc",
  "Subject",
  "Message-ID",
  "Reply-To",
  "X-Spam-Flag",
  "X-Spam-Score",
  "Body",
];

/** Common Set keys (X-OTRS-*). Free-text still allowed — Znuny does not hard-restrict. */
const SET_KEY_SUGGESTIONS = [
  "X-OTRS-Queue",
  "X-OTRS-Priority",
  "X-OTRS-State",
  "X-OTRS-Type",
  "X-OTRS-Ignore",
  "X-OTRS-Lock",
  "X-OTRS-Owner",
  "X-OTRS-Responsible",
  "X-OTRS-CustomerNo",
  "X-OTRS-CustomerUser",
  "X-OTRS-Service",
  "X-OTRS-SLA",
];

function stopFromRules(filter: PostmasterFilterOut): boolean {
  return filter.rules.some((r) => Boolean(r.f_stop));
}

function matchCount(filter: PostmasterFilterOut): number {
  return filter.rules.filter((r) => r.f_type === "Match").length;
}

function setCount(filter: PostmasterFilterOut): number {
  return filter.rules.filter((r) => r.f_type === "Set").length;
}

function formFromFilter(filter: PostmasterFilterOut | null): FieldValues {
  if (!filter) {
    return {
      name: "",
      stop: false,
      match: [{ key: "From", value: "", negate: false }] as MatchRow[],
      set: [{ key: "X-OTRS-Queue", value: "" }] as SetRow[],
    };
  }
  return {
    name: filter.name,
    stop: stopFromRules(filter),
    match: filter.rules
      .filter((r) => r.f_type === "Match")
      .map((r) => ({
        key: r.f_key,
        value: r.f_value,
        negate: Boolean(r.f_not),
      })) as MatchRow[],
    set: filter.rules
      .filter((r) => r.f_type === "Set")
      .map((r) => ({ key: r.f_key, value: r.f_value })) as SetRow[],
  };
}

function toWriteBody(values: FieldValues): PostmasterFilterWrite {
  const match = (Array.isArray(values.match) ? values.match : []) as MatchRow[];
  const set = (Array.isArray(values.set) ? values.set : []) as SetRow[];
  return {
    name: String(values.name ?? "").trim(),
    stop: Boolean(values.stop),
    match: match
      .map((m) => ({
        key: String(m.key ?? "").trim(),
        value: String(m.value ?? ""),
        negate: Boolean(m.negate),
      }))
      .filter((m) => m.key && m.value),
    set: set
      .map((s) => ({
        key: String(s.key ?? "").trim(),
        value: String(s.value ?? ""),
      }))
      .filter((s) => s.key && s.value),
  };
}

function RuleListEditor<T extends Record<string, unknown>>({
  rows,
  onChange,
  columns,
  addLabel,
  removeLabel,
  emptyRow,
  testIdPrefix,
}: {
  rows: T[];
  onChange: (next: T[]) => void;
  columns: Array<{
    key: keyof T & string;
    label: string;
    type: "text" | "checkbox";
    listId?: string;
    placeholder?: string;
  }>;
  addLabel: string;
  removeLabel: string;
  emptyRow: T;
  testIdPrefix: string;
}) {
  const inputClass =
    "w-full rounded-md border border-hairline bg-surface-subtle px-2 py-1 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

  return (
    <div className="space-y-2" data-testid={testIdPrefix}>
      {rows.map((row, idx) => (
        <div
          key={idx}
          className="flex flex-wrap items-end gap-2 rounded-md border border-hairline bg-surface-subtle/40 p-2"
          data-testid={`${testIdPrefix}-row-${idx}`}
        >
          {columns.map((col) => (
            <label key={col.key} className="min-w-[7rem] flex-1">
              <span className="mb-0.5 block text-[10px] font-medium uppercase tracking-wide text-muted">
                {col.label}
              </span>
              {col.type === "checkbox" ? (
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4 accent-accent"
                  checked={Boolean(row[col.key])}
                  onChange={(e) => {
                    const next = rows.map((r, i) =>
                      i === idx ? { ...r, [col.key]: e.target.checked } : r,
                    );
                    onChange(next);
                  }}
                  data-testid={`${testIdPrefix}-${col.key}-${idx}`}
                />
              ) : (
                <input
                  type="text"
                  list={col.listId}
                  placeholder={col.placeholder}
                  className={inputClass}
                  value={String(row[col.key] ?? "")}
                  onChange={(e) => {
                    const next = rows.map((r, i) =>
                      i === idx ? { ...r, [col.key]: e.target.value } : r,
                    );
                    onChange(next);
                  }}
                  data-testid={`${testIdPrefix}-${col.key}-${idx}`}
                />
              )}
            </label>
          ))}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid={`${testIdPrefix}-remove-${idx}`}
            onClick={() => onChange(rows.filter((_, i) => i !== idx))}
            disabled={rows.length <= 1 && testIdPrefix.includes("match")}
          >
            {removeLabel}
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="secondary"
        size="sm"
        data-testid={`${testIdPrefix}-add`}
        onClick={() => onChange([...rows, { ...emptyRow }])}
      >
        {addLabel}
      </Button>
    </div>
  );
}

export function PostmasterFiltersPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<PostmasterFilterOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["admin", "postmaster-filters"],
    queryFn: ({ signal }) => api.listPostmasterFilters(signal),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["admin", "postmaster-filters"] });

  const createM = useMutation({
    mutationFn: (body: PostmasterFilterWrite) => api.createPostmasterFilter(body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const updateM = useMutation({
    mutationFn: ({ name, body }: { name: string; body: PostmasterFilterWrite }) =>
      api.updatePostmasterFilter(name, body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const deleteM = useMutation({
    mutationFn: (name: string) => api.deletePostmasterFilter(name),
    onSuccess: () => invalidate(),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };

  const openEdit = (row: PostmasterFilterOut) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const handleDelete = (row: PostmasterFilterOut) => {
    const ok = window.confirm(
      t("admin.postmasterFilters.deleteConfirm", { name: row.name }),
    );
    if (!ok) return;
    deleteM.mutate(row.name);
  };

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    const body = toWriteBody(values);
    if (!body.name) {
      setFormError(t("admin.form.required"));
      throw new Error("name required");
    }
    if (!body.match.length) {
      setFormError(t("admin.postmasterFilters.matchRequired"));
      throw new Error("match required");
    }
    try {
      if (editing) {
        await updateM.mutateAsync({ name: editing.name, body });
      } else {
        await createM.mutateAsync(body);
      }
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      throw err;
    }
  };

  const columns: DataTableColumn<PostmasterFilterOut>[] = [
    {
      key: "name",
      header: t("admin.postmasterFilters.name"),
      render: (r) => (
        <Link
          to="/admin/postmaster-filters/$name"
          params={{ name: r.name }}
          className="text-accent hover:underline"
          data-testid={`postmaster-filter-link-${r.name}`}
        >
          {r.name}
        </Link>
      ),
    },
    {
      key: "match",
      header: t("admin.postmasterFilters.matchCount"),
      render: (r) => <Badge tone="muted">{matchCount(r)}</Badge>,
    },
    {
      key: "set",
      header: t("admin.postmasterFilters.setCount"),
      render: (r) => <Badge tone="muted">{setCount(r)}</Badge>,
    },
    {
      key: "stop",
      header: t("admin.postmasterFilters.stop"),
      render: (r) =>
        stopFromRules(r) ? (
          <Badge tone="warn">{t("admin.postmasterFilters.stopYes")}</Badge>
        ) : (
          <span className="text-xs text-muted">—</span>
        ),
    },
  ];

  const fields: FieldDef[] = [
    {
      name: "name",
      label: t("admin.postmasterFilters.name"),
      type: "text",
      required: true,
    },
    {
      name: "stop",
      label: t("admin.postmasterFilters.stopAfterMatch"),
      type: "checkbox",
      helpText: t("admin.postmasterFilters.stopHelp"),
    },
    {
      name: "match",
      label: t("admin.postmasterFilters.matchRules"),
      type: "custom",
      required: true,
      render: (value, onChange) => (
        <RuleListEditor<MatchRow>
          rows={(Array.isArray(value) ? value : []) as MatchRow[]}
          onChange={onChange as (next: MatchRow[]) => void}
          columns={[
            {
              key: "key",
              label: t("admin.postmasterFilters.header"),
              type: "text",
              listId: "pm-match-headers",
              placeholder: "From",
            },
            {
              key: "value",
              label: t("admin.postmasterFilters.value"),
              type: "text",
              placeholder: t("admin.postmasterFilters.valuePlaceholder"),
            },
            {
              key: "negate",
              label: t("admin.postmasterFilters.negate"),
              type: "checkbox",
            },
          ]}
          addLabel={t("admin.postmasterFilters.addMatch")}
          removeLabel={t("admin.postmasterFilters.remove")}
          emptyRow={{ key: "", value: "", negate: false }}
          testIdPrefix="admin-pm-match"
        />
      ),
    },
    {
      name: "set",
      label: t("admin.postmasterFilters.setRules"),
      type: "custom",
      render: (value, onChange) => (
        <RuleListEditor<SetRow>
          rows={(Array.isArray(value) ? value : []) as SetRow[]}
          onChange={onChange as (next: SetRow[]) => void}
          columns={[
            {
              key: "key",
              label: t("admin.postmasterFilters.setKey"),
              type: "text",
              listId: "pm-set-keys",
              placeholder: "X-OTRS-Queue",
            },
            {
              key: "value",
              label: t("admin.postmasterFilters.value"),
              type: "text",
            },
          ]}
          addLabel={t("admin.postmasterFilters.addSet")}
          removeLabel={t("admin.postmasterFilters.remove")}
          emptyRow={{ key: "", value: "" }}
          testIdPrefix="admin-pm-set"
        />
      ),
    },
  ];

  const rows = listQ.data ?? [];

  return (
    <div className="space-y-3 p-4" data-testid="admin-postmaster-filters-page">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.postmasterFilters.title_plural")}
        </h1>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-postmaster-filters-new"
          onClick={openCreate}
        >
          {t("admin.postmasterFilters.new")}
        </Button>
      </div>

      <p className="text-xs text-muted">{t("admin.postmasterFilters.hint")}</p>

      <datalist id="pm-match-headers">
        {MATCH_HEADER_SUGGESTIONS.map((h) => (
          <option key={h} value={h} />
        ))}
      </datalist>
      <datalist id="pm-set-keys">
        {SET_KEY_SUGGESTIONS.map((k) => (
          <option key={k} value={k} />
        ))}
      </datalist>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.name}
        isLoading={listQ.isLoading}
        emptyLabel={t("admin.postmasterFilters.empty")}
        onEdit={openEdit}
        onDeactivate={handleDelete}
        testId="admin-postmaster-filters-table"
      />

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={
          editing
            ? t("admin.form.editTitle", { title: t("admin.postmasterFilters.title_plural") })
            : t("admin.postmasterFilters.new")
        }
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={formFromFilter(editing)}
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-postmaster-filters-form"
      />
    </div>
  );
}
