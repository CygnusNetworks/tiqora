import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AdminListState } from "@/components/itsm/change/AdminListState";
import { useDeleteConfirm } from "@/components/itsm/change/useDeleteConfirm";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { changeAdminApi, changeAdminKeys, type AdminCab, type AdminCabMember } from "@/lib/changeAdminApi";
import { changeKeys } from "@/lib/changeApi";
import { settingsSectionsApi } from "@/lib/settingsSectionsApi";
import { CabDialog } from "./CabDialog";

function MemberList({ members }: { members: AdminCabMember[] }) {
  const { t } = useTranslation();
  if (members.length === 0) return <>{t("admin.itsmChange.cab.membersEmpty")}</>;
  return (
    <>
      {members.map((m, i) => (
        <span key={`${m.member_kind}:${m.member_ref}`}>
          {i > 0 && ", "}
          {m.count != null ? t("admin.itsmChange.cab.memberCount", { label: m.label, count: m.count }) : m.label}
          {m.missing && (
            <>
              {" "}
              <Badge tone="danger" data-testid="cab-member-missing">
                {t("admin.itsmChange.cab.missing")}
              </Badge>
            </>
          )}
        </span>
      ))}
    </>
  );
}

/** The CAB tab: boards (rule, emergency flag, members); create, edit and delete them. */
export function ChangeCabTab() {
  const { t } = useTranslation();
  const [editor, setEditor] = useState<{ cab: AdminCab | null } | null>(null);
  const q = useQuery({ queryKey: changeAdminKeys.cabs(), queryFn: ({ signal }) => changeAdminApi.cabs(signal) });
  const del = useDeleteConfirm<AdminCab>({
    remove: (cab) => changeAdminApi.deleteCab(cab.id),
    invalidate: [changeAdminKeys.cabs(), changeKeys.cabs()],
    title: t("admin.itsmChange.cab.deleteTitle"),
    message: (cab) => t("admin.itsmChange.cab.deleteMessage", { name: cab.name }),
    errorTestId: "cab-delete-error",
  });
  const cabs = q.data ?? [];
  // CX21: customer users may be members only while the engine lets customers approve
  // (`approvals.customer_approvers_enabled`). Read from the approvals settings section, so it is right
  // with no CAB yet; every CabOut carries the same value, the fallback while the section is unreadable.
  const approvalsQ = useQuery({
    queryKey: ["settings-sections", "approvals"],
    queryFn: ({ signal }) => settingsSectionsApi.get("approvals", signal),
    retry: false,
    staleTime: 60_000,
  });
  const fromSettings = approvalsQ.data?.values?.customer_approvers_enabled;
  const customersAllowed = (cab: AdminCab | null) =>
    typeof fromSettings === "boolean" ? fromSettings : ((cab ?? cabs[0])?.customer_members_allowed ?? false);

  return (
    <section className="space-y-3" data-testid="change-cab-tab">
      <div className="flex justify-end">
        <Button variant="primary" onClick={() => setEditor({ cab: null })} data-testid="cab-new">
          {t("admin.itsmChange.cab.new")}
        </Button>
      </div>
      <AdminListState query={q} count={cabs.length} emptyText={t("admin.itsmChange.cab.empty")} testId="cabs">
          <ul className="divide-y divide-hairline rounded-md border border-hairline bg-surface">
            {cabs.map((cab) => (
              <li key={cab.id} className="flex flex-wrap items-start justify-between gap-2 px-3 py-2" data-testid="cab-row" data-cab-id={cab.id}>
                <div className="min-w-0 flex-1 space-y-0.5">
                  <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-ink">
                    <span className="min-w-0 break-words">{cab.name}</span>
                    {cab.is_emergency && (
                      <Badge tone="warn" data-testid="cab-emergency">
                        {t("admin.itsmChange.cab.emergency")}
                      </Badge>
                    )}
                    {!cab.valid && <Badge tone="muted">{t("admin.itsmChange.invalid")}</Badge>}
                  </p>
                  {cab.description && <p className="break-words text-xs text-muted">{cab.description}</p>}
                  <p className="text-xs text-muted" data-testid="cab-row-rule">
                    {t(`admin.itsmChange.cab.rule.${cab.rule}`)}
                    {cab.rule === "quorum" && cab.quorum != null && `: ${cab.quorum}`}
                  </p>
                  <p className="break-words text-xs text-ink" data-testid="cab-row-members">
                    <MemberList members={cab.members} />
                  </p>
                  {cab.members.length === 0 && (
                    <p className="text-xs text-escalation" data-testid="cab-row-empty-hint">
                      {t("admin.itsmChange.cab.emptyHint")}
                    </p>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setEditor({ cab })} data-testid="cab-edit">
                    {t("common.edit")}
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => del.ask(cab)} data-testid="cab-delete">
                    {t("common.delete")}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
      </AdminListState>

      {editor && (
        <CabDialog
          key={editor.cab?.id ?? "new"}
          cab={editor.cab}
          customersAllowed={customersAllowed(editor.cab)}
          onClose={() => setEditor(null)}
        />
      )}
      {del.dialog}
    </section>
  );
}
