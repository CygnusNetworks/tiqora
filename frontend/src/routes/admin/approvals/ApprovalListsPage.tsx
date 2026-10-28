import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { Switch } from "@/components/ui/Switch";
import { AgentSelect, inputCls } from "@/components/approvals/pickers";
import { api } from "@/lib/api";
import { approvalErrorMessage, approvalsKeys } from "@/lib/approvalsApi";
import { approvalsAdminApi, approvalsAdminKeys, type ApprovalList } from "@/lib/approvalsAdminApi";
import { AdminApprovalsHeader } from "./AdminApprovalsHeader";

function ListDialog({ list, onClose }: { list: ApprovalList | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [name, setName] = useState(list?.name ?? "");
  const [description, setDescription] = useState(list?.description ?? "");
  const [valid, setValid] = useState(list?.valid ?? true);
  const [memberIds, setMemberIds] = useState<number[]>(list?.member_ids ?? []);
  const [touched, setTouched] = useState(false);

  // Names of members the list already had come with the list; new ones from the agent reference data.
  const agents = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: ({ signal }) => api.listReferenceAgents(signal),
    staleTime: 60_000,
  });
  const label = (id: number) => {
    const known = list?.members.find((m) => m.id === id)?.label;
    if (known) return known;
    const agent = agents.data?.find((a) => a.id === id);
    return agent ? agent.full_name || agent.login : `#${id}`;
  };

  const save = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), description: description.trim() || null, valid, member_ids: memberIds };
      return list ? approvalsAdminApi.updateList(list.id, body) : approvalsAdminApi.createList(body);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: approvalsAdminKeys.lists() });
      void qc.invalidateQueries({ queryKey: approvalsKeys.lists() });
      onClose();
    },
  });
  const submit = () => {
    setTouched(true);
    if (name.trim() === "" || save.isPending) return;
    save.mutate();
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title={list ? t("approvals.admin.lists.edit") : t("approvals.admin.lists.new")}
      footer={
        <>
          <Button onClick={onClose} data-testid="list-cancel">
            {t("common.cancel")}
          </Button>
          <Button variant="primary" disabled={save.isPending} onClick={submit} data-testid="list-save">
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4" data-testid="list-dialog">
        <label className="block text-xs font-medium text-muted">
          {t("approvals.admin.lists.name")}
          <input className={`${inputCls} mt-1`} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} aria-invalid={touched && name.trim() === ""} data-testid="list-name" />
          {touched && name.trim() === "" && (
            <span className="mt-1 block text-danger" role="alert" data-testid="list-name-error">
              {t("approvals.admin.lists.nameMissing")}
            </span>
          )}
        </label>
        <label className="block text-xs font-medium text-muted">
          {t("approvals.admin.lists.description")}
          <textarea className={`${inputCls} mt-1`} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} data-testid="list-description" />
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <Switch checked={valid} onChange={setValid} testId="list-valid" aria-label={t("approvals.admin.lists.valid")} />
          {t("approvals.admin.lists.valid")}
        </label>

        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">{t("approvals.admin.lists.members")}</h3>
          {memberIds.length === 0 ? (
            <p className="text-xs text-muted" data-testid="list-members-empty">
              {t("approvals.admin.lists.membersEmpty")}
            </p>
          ) : (
            <ul className="divide-y divide-hairline rounded-md border border-hairline" data-testid="list-members">
              {memberIds.map((id) => (
                <li key={id} className="flex items-center justify-between gap-2 px-3 py-1.5 text-sm" data-testid="list-member" data-member-id={id}>
                  <span className="min-w-0 truncate text-ink">{label(id)}</span>
                  <Button size="sm" variant="ghost" onClick={() => setMemberIds(memberIds.filter((m) => m !== id))} aria-label={t("approvals.admin.lists.removeMember", { name: label(id) })} data-testid="list-member-remove">
                    ✕
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <div className="text-xs text-muted">
            {t("approvals.admin.lists.addMember")}
            <div className="mt-1">
              <AgentSelect
                value={null}
                onChange={(id) => setMemberIds((cur) => (cur.includes(id) ? cur : [...cur, id]))}
                testId="list-member-add"
                ariaLabel={t("approvals.admin.lists.addMember")}
              />
            </div>
          </div>
        </div>

        {save.isError && (
          <p className="text-sm text-danger" role="alert" data-testid="list-save-error">
            {approvalErrorMessage(save.error, t)}
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** Freigabegruppen: named groups of agents (a CAB, a board) that templates and requests can name as approvers. */
export function ApprovalListsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editor, setEditor] = useState<{ list: ApprovalList | null } | null>(null);
  const [deleting, setDeleting] = useState<ApprovalList | null>(null);
  const q = useQuery({
    queryKey: approvalsAdminKeys.lists(),
    queryFn: ({ signal }) => approvalsAdminApi.lists(signal),
  });
  const remove = useMutation({
    mutationFn: (l: ApprovalList) => approvalsAdminApi.deleteList(l.id),
    onSuccess: () => {
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: approvalsAdminKeys.lists() });
      void qc.invalidateQueries({ queryKey: approvalsKeys.lists() });
    },
  });
  const rows = q.data ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-approval-lists-page">
      <AdminApprovalsHeader
        title={t("admin.nav.approvalsLists")}
        action={
          <Button variant="primary" onClick={() => setEditor({ list: null })} data-testid="list-new">
            {t("approvals.admin.lists.new")}
          </Button>
        }
      />
      {q.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="lists-error">
          {t("approvals.agent.loadFailed")}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted" data-testid="lists-empty">
          {q.isPending ? t("common.loading") : t("approvals.admin.lists.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline rounded-md border border-hairline bg-surface" data-testid="lists-list">
          {rows.map((l) => (
            <li key={l.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2" data-testid="list-row" data-list-id={l.id}>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">
                  {l.name}{" "}
                  {!l.valid && (
                    <Badge tone="warn" data-testid="list-invalid">
                      {t("approvals.admin.lists.invalid")}
                    </Badge>
                  )}
                </p>
                {l.description && <p className="truncate text-xs text-muted">{l.description}</p>}
                <p className="truncate text-xs text-muted" data-testid="list-row-members">
                  {l.members.length === 0 ? t("approvals.admin.lists.membersEmpty") : l.members.map((m) => m.label).join(", ")}
                </p>
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => setEditor({ list: l })} data-testid="list-edit">
                  {t("common.edit")}
                </Button>
                <Button size="sm" variant="danger" onClick={() => setDeleting(l)} data-testid="list-delete">
                  {t("common.delete")}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {editor && <ListDialog key={editor.list?.id ?? "new"} list={editor.list} onClose={() => setEditor(null)} />}
      <ConfirmDialog
        open={deleting !== null}
        title={t("approvals.admin.lists.deleteTitle")}
        message={
          <>
            <p>{t("approvals.admin.lists.deleteMessage", { name: deleting?.name ?? "" })}</p>
            {remove.isError && (
              <p className="mt-2 text-danger" role="alert" data-testid="list-delete-error">
                {approvalErrorMessage(remove.error, t)}
              </p>
            )}
          </>
        }
        variant="danger"
        confirmLabel={t("common.delete")}
        pending={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting)}
        onCancel={() => {
          remove.reset();
          setDeleting(null);
        }}
      />
    </div>
  );
}
