import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import {
  aiApi,
  type AclFeature,
  type AclSubjectType,
  type AiAclOut,
} from "@/lib/aiApi";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { Spinner } from "@/components/ui/Spinner";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";
import { SelectField } from "@/components/ui/SelectField";
import { PlusIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";

const ACL_KEY = ["admin", "ai", "acl"] as const;

const ACL_SUBJECT_TYPES: AclSubjectType[] = ["group", "role", "user"];
const ACL_FEATURES: AclFeature[] = ["summary", "auto_reply", "manual_assist", "mcp"];

function toAclFormValues(row: AiAclOut | null): FieldValues {
  return row
    ? {
        subject_type: row.subject_type,
        subject_id: row.subject_id,
        feature: row.feature,
        allowed: row.allowed,
        limit_requests_day: row.limit_requests_day ?? "",
        limit_tokens_day: row.limit_tokens_day ?? "",
        limit_requests_month: row.limit_requests_month ?? "",
      }
    : {
        subject_type: "group",
        subject_id: "",
        feature: "auto_reply",
        allowed: true,
        limit_requests_day: "",
        limit_tokens_day: "",
        limit_requests_month: "",
      };
}

/** KI-Zugriff & Limits (ACL) — extracted from AiSettingsPage into its own
 * admin menu item; subjects are picked by NAME via reference lookups. */
export function AiAclPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [aclDrawerOpen, setAclDrawerOpen] = useState(false);
  const [editingAcl, setEditingAcl] = useState<AiAclOut | null>(null);
  const [aclFormError, setAclFormError] = useState<string | null>(null);

  const aclQ = useQuery({
    queryKey: ACL_KEY,
    queryFn: ({ signal }) => aiApi.listAcl(signal),
  });
  // Subject lookups so ACL entries can be picked by NAME instead of raw id.
  const groupsQ = useQuery({
    queryKey: ["admin", "ai", "acl-ref", "groups"],
    queryFn: () => api.adminGroups.list({ page: 1, pageSize: 500 }),
  });
  const rolesQ = useQuery({
    queryKey: ["admin", "ai", "acl-ref", "roles"],
    queryFn: () => api.adminRoles.list({ page: 1, pageSize: 500 }),
  });
  const agentsQ = useQuery({
    queryKey: ["admin", "ai", "acl-ref", "agents"],
    queryFn: ({ signal }) => api.listReferenceAgents(signal),
  });

  const subjectItems = (subjectType: string) => {
    if (subjectType === "group")
      return (groupsQ.data?.items ?? []).map((g) => ({ value: g.id, label: g.name }));
    if (subjectType === "role")
      return (rolesQ.data?.items ?? []).map((r) => ({ value: r.id, label: r.name }));
    return (agentsQ.data ?? []).map((a) => ({
      value: a.id,
      label: a.full_name,
      hint: a.login,
    }));
  };

  const subjectLabel = (subjectType: string, subjectId: number) => {
    const hit = subjectItems(subjectType).find((i) => i.value === subjectId);
    return hit ? hit.label : `#${subjectId}`;
  };

  const invalidateAcl = () => qc.invalidateQueries({ queryKey: ACL_KEY });

  const createAclM = useMutation({
    mutationFn: (values: FieldValues) =>
      aiApi.createAcl({
        subject_type: values.subject_type as AclSubjectType,
        subject_id: Number(values.subject_id),
        feature: values.feature as AclFeature,
        allowed: Boolean(values.allowed),
        limit_requests_day: values.limit_requests_day ? Number(values.limit_requests_day) : null,
        limit_tokens_day: values.limit_tokens_day ? Number(values.limit_tokens_day) : null,
        limit_requests_month: values.limit_requests_month
          ? Number(values.limit_requests_month)
          : null,
      }),
    onSuccess: async () => {
      setAclDrawerOpen(false);
      await invalidateAcl();
    },
  });

  const updateAclM = useMutation({
    mutationFn: ({ id, values }: { id: number; values: FieldValues }) =>
      aiApi.updateAcl(id, {
        subject_type: values.subject_type as AclSubjectType,
        subject_id: Number(values.subject_id),
        feature: values.feature as AclFeature,
        allowed: Boolean(values.allowed),
        limit_requests_day: values.limit_requests_day ? Number(values.limit_requests_day) : null,
        limit_tokens_day: values.limit_tokens_day ? Number(values.limit_tokens_day) : null,
        limit_requests_month: values.limit_requests_month
          ? Number(values.limit_requests_month)
          : null,
      }),
    onSuccess: async () => {
      setAclDrawerOpen(false);
      await invalidateAcl();
    },
  });

  const deleteAclM = useMutation({
    mutationFn: (id: number) => aiApi.deleteAcl(id),
    onSuccess: () => invalidateAcl(),
  });

  const openAclCreate = () => {
    setEditingAcl(null);
    setAclFormError(null);
    setAclDrawerOpen(true);
  };
  const openAclEdit = (row: AiAclOut) => {
    setEditingAcl(row);
    setAclFormError(null);
    setAclDrawerOpen(true);
  };

  const handleAclSubmit = async (values: FieldValues) => {
    setAclFormError(null);
    // Guard against a stale subject_id after switching the subject type —
    // the picker keeps the old numeric value, which would silently target
    // the wrong entity of the new type.
    const subjectType = String(values.subject_type ?? "group");
    const subjectId = Number(values.subject_id);
    if (!subjectItems(subjectType).some((i) => i.value === subjectId)) {
      setAclFormError(t("admin.ai.acl.subjectMismatch"));
      throw new Error("subject mismatch");
    }
    try {
      if (editingAcl) {
        await updateAclM.mutateAsync({ id: editingAcl.id, values });
      } else {
        await createAclM.mutateAsync(values);
      }
    } catch (err) {
      setAclFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      throw err;
    }
  };

  const limitSummary = (r: AiAclOut) =>
    [
      r.limit_requests_day != null ? `${r.limit_requests_day}/d req` : null,
      r.limit_tokens_day != null ? `${r.limit_tokens_day}/d tok` : null,
      r.limit_requests_month != null ? `${r.limit_requests_month}/mo req` : null,
    ]
      .filter(Boolean)
      .join(" · ") || t("admin.ai.acl.noLimits");

  // Two-line provider-style row: allow-dot + subject + feature on top, limits
  // (mono) below, allowed/denied badge right, edit/delete in the ⋯-menu.
  const renderAclRow = (r: AiAclOut) => (
    <div key={r.id} className="border-t border-hairline first:border-t-0">
      <div
        role="button"
        tabIndex={0}
        data-testid={`admin-ai-acl-row-${r.id}`}
        onClick={() => openAclEdit(r)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openAclEdit(r);
          }
        }}
        className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 px-3.5 py-2.5 transition-colors hover:bg-surface-subtle md:grid-cols-[minmax(0,1fr)_auto_auto]"
      >
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className={cn(
              "h-1.5 w-1.5 shrink-0 rounded-full",
              r.allowed ? "bg-green" : "bg-danger",
            )}
            title={r.allowed ? t("admin.ai.acl.allowedYes") : t("admin.ai.acl.allowedNo")}
          />
          <span className="truncate text-sm font-medium text-ink">
            {t(`admin.ai.acl.subjectType.${r.subject_type}`)}:{" "}
            {subjectLabel(r.subject_type, r.subject_id)}
          </span>
          <span className="shrink-0 text-xs text-muted">{t(`admin.ai.feature.${r.feature}`)}</span>
        </div>
        <div className="col-start-1 row-start-2 flex min-w-0 items-baseline gap-3 pl-4">
          <span className="truncate font-mono text-xs text-muted">{limitSummary(r)}</span>
        </div>
        <div className="row-span-2 hidden items-center justify-end md:flex">
          <Badge tone={r.allowed ? "success" : "danger"}>
            {r.allowed ? t("admin.ai.acl.allowedYes") : t("admin.ai.acl.allowedNo")}
          </Badge>
        </div>
        <div
          className="col-start-2 row-span-2 md:col-start-3"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <Menu
            panelTestId={`admin-ai-acl-menu-${r.id}`}
            trigger={({ ref, toggleProps }) => (
              <button
                type="button"
                ref={ref}
                {...toggleProps}
                data-testid={`admin-ai-acl-menu-trigger-${r.id}`}
                title={t("admin.table.actions")}
                className="rounded-md px-1.5 py-1 text-sm leading-none text-muted transition-colors hover:bg-surface-subtle hover:text-ink"
              >
                ⋯
              </button>
            )}
          >
            <MenuItem testId={`admin-ai-acl-edit-${r.id}`} onSelect={() => openAclEdit(r)}>
              {t("admin.table.edit")}
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              danger
              testId={`admin-row-delete-${r.id}`}
              onSelect={() => deleteAclM.mutate(r.id)}
            >
              {t("admin.table.delete")}
            </MenuItem>
          </Menu>
        </div>
      </div>
    </div>
  );

  const aclFields: FieldDef[] = [
    {
      name: "subject_type",
      label: t("admin.ai.acl.subjectType.label"),
      type: "select",
      required: true,
      options: ACL_SUBJECT_TYPES.map((v) => ({ value: v, label: t(`admin.ai.acl.subjectType.${v}`) })),
    },
    {
      name: "subject_id",
      label: t("admin.ai.acl.subject"),
      type: "custom",
      required: true,
      helpText: t("admin.ai.acl.subjectIdHelp"),
      render: (value, onChange, values) => (
        <SelectField
          items={subjectItems(String(values.subject_type ?? "group"))}
          value={typeof value === "number" ? value : null}
          onChange={(v) => onChange(v)}
          placeholder={t("admin.form.selectPlaceholder")}
          testId="admin-ai-acl-form-subject_id"
        />
      ),
    },
    {
      name: "feature",
      label: t("admin.ai.acl.feature"),
      type: "select",
      required: true,
      options: ACL_FEATURES.map((v) => ({ value: v, label: t(`admin.ai.feature.${v}`) })),
    },
    { name: "allowed", label: t("admin.ai.acl.allowed"), type: "checkbox" },
    { name: "limit_requests_day", label: t("admin.ai.acl.limitRequestsDay"), type: "number" },
    { name: "limit_tokens_day", label: t("admin.ai.acl.limitTokensDay"), type: "number" },
    { name: "limit_requests_month", label: t("admin.ai.acl.limitRequestsMonth"), type: "number" },
  ];

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4" data-testid="admin-ai-acl-page">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.ai.acl.title")}</h1>
          <p className="mt-1 text-sm text-muted">{t("admin.ai.acl.description")}</p>
        </div>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-ai-acl-new"
          onClick={openAclCreate}
          aria-label={t("admin.ai.acl.new")}
          title={t("admin.ai.acl.new")}
          className="!px-2"
        >
          <PlusIcon className="text-[16px]" />
        </Button>
      </div>
      <div
        className="rounded-xl border border-hairline bg-surface"
        data-testid="admin-ai-acl-table"
      >
        {aclQ.isLoading ? (
          <div className="flex justify-center p-6">
            <Spinner />
          </div>
        ) : (aclQ.data?.length ?? 0) === 0 ? (
          <p className="p-4 text-sm text-muted">{t("admin.table.empty")}</p>
        ) : (
          (aclQ.data ?? []).map(renderAclRow)
        )}
      </div>

      <CrudDrawer
        open={aclDrawerOpen}
        onClose={() => setAclDrawerOpen(false)}
        title={editingAcl ? t("admin.form.editTitle", { title: t("admin.ai.acl.title") }) : t("admin.ai.acl.new")}
        fields={aclFields}
        mode={editingAcl ? "edit" : "create"}
        initialValues={toAclFormValues(editingAcl)}
        onSubmit={handleAclSubmit}
        submitError={aclFormError}
        testIdPrefix="admin-ai-acl-form"
      />
    </div>
  );
}
