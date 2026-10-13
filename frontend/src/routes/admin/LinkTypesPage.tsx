import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { itsmApi, itsmErrorCode, itsmKeys, type LinkType, type LinkTypePatchIn } from "@/lib/itsmApi";

type Propagation = LinkType["propagation"];
const PROPAGATIONS: Propagation[] = ["none", "source", "target", "both"];
const OFFER_FLAGS = ["offer_ticket_ticket", "offer_ticket_ci", "offer_ci_ci", "offer_ci_service"] as const;

type Draft = {
  source_label: string;
  target_label: string;
  valid: boolean;
  propagation: Propagation;
  offer_ticket_ticket: boolean;
  offer_ticket_ci: boolean;
  offer_ci_ci: boolean;
  offer_ci_service: boolean;
};

const draftOf = (row: LinkType): Draft => ({
  source_label: row.source_label ?? "",
  target_label: row.target_label ?? "",
  valid: row.valid,
  propagation: row.propagation,
  offer_ticket_ticket: row.offer_ticket_ticket,
  offer_ticket_ci: row.offer_ticket_ci,
  offer_ci_ci: row.offer_ci_ci,
  offer_ci_service: row.offer_ci_service,
});

/** Only the keys that differ from the row (`propagation` only for Tiqora types and when shown). */
function patchOf(row: LinkType, d: Draft, propagationEditable: boolean): LinkTypePatchIn {
  const patch: LinkTypePatchIn = {};
  const label = (v: string) => (v.trim() === "" ? null : v.trim());
  if (label(d.source_label) !== row.source_label) patch.source_label = label(d.source_label);
  if (label(d.target_label) !== row.target_label) patch.target_label = label(d.target_label);
  if (d.valid !== row.valid) patch.valid = d.valid;
  for (const flag of OFFER_FLAGS) if (d[flag] !== row[flag]) patch[flag] = d[flag];
  if (propagationEditable && d.propagation !== row.propagation) patch.propagation = d.propagation;
  return patch;
}

function EditDialog({
  row,
  propagationEditable,
  onClose,
}: {
  row: LinkType;
  propagationEditable: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftOf(row));
  const save = useMutation({
    mutationFn: () => itsmApi.admin.patchLinkType(row.id, patchOf(row, draft, propagationEditable)),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: itsmKeys.adminLinkTypes() });
      await qc.invalidateQueries({ queryKey: ["itsm", "link-types"] });
      onClose();
    },
  });
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((d) => ({ ...d, [key]: value }));
  const code = save.isError ? itsmErrorCode(save.error) : null;

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("admin.itsm.linkTypes.editTitle", { name: row.name })}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending} data-testid="link-type-save">
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm" data-testid="link-type-dialog">
        <label className="block">
          <span className="text-xs text-muted">{t("admin.itsm.linkTypes.sourceLabel")}</span>
          <input
            className="mt-1 w-full rounded border border-hairline bg-surface px-2 py-1.5 text-ink"
            value={draft.source_label}
            maxLength={100}
            onChange={(e) => set("source_label", e.target.value)}
            data-testid="link-type-source-label"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted">{t("admin.itsm.linkTypes.targetLabel")}</span>
          <input
            className="mt-1 w-full rounded border border-hairline bg-surface px-2 py-1.5 text-ink"
            value={draft.target_label}
            maxLength={100}
            onChange={(e) => set("target_label", e.target.value)}
            data-testid="link-type-target-label"
          />
        </label>
        <fieldset className="space-y-1">
          <legend className="text-xs text-muted">{t("admin.itsm.linkTypes.offer")}</legend>
          {OFFER_FLAGS.map((flag) => (
            <label key={flag} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={draft[flag]}
                onChange={(e) => set(flag, e.target.checked)}
                data-testid={`link-type-${flag}`}
              />
              {t(`admin.itsm.linkTypes.flags.${flag}`)}
            </label>
          ))}
        </fieldset>
        {propagationEditable && (
          <label className="block">
            <span className="text-xs text-muted">{t("admin.itsm.linkTypes.propagation")}</span>
            <SelectField
              items={PROPAGATIONS.map((p) => ({ value: p, label: t(`admin.itsm.linkTypes.propagations.${p}`) }))}
              value={draft.propagation}
              onChange={(p) => set("propagation", p)}
              testId="link-type-propagation"
            />
          </label>
        )}
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={draft.valid}
            onChange={(e) => set("valid", e.target.checked)}
            data-testid="link-type-valid"
          />
          {t("admin.itsm.linkTypes.valid")}
        </label>
        {save.isError && (
          <p role="alert" className="text-danger" data-testid="link-type-error">
            {t(`admin.itsm.linkTypes.errors.${code ?? "generic"}`, { defaultValue: t("admin.itsm.error") })}
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** `/admin/itsm/link-types`: link-type editor (one table for tickets, CIs and services). */
export function LinkTypesPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<LinkType | null>(null);
  const list = useQuery({ queryKey: itsmKeys.adminLinkTypes(), queryFn: ({ signal }) => itsmApi.admin.linkTypes(signal) });
  const admin = useQuery({ queryKey: itsmKeys.admin(), queryFn: ({ signal }) => itsmApi.admin.get(signal) });
  const sync = useMutation({
    mutationFn: () => itsmApi.admin.syncLinkTypes(),
    onSuccess: () => qc.invalidateQueries({ queryKey: itsmKeys.adminLinkTypes() }),
  });
  const propagationShown = admin.data?.mode === "tiqora";

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-itsm-link-types-page">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.itsm.linkTypes.title")}</h1>
        <Button onClick={() => sync.mutate()} disabled={sync.isPending} data-testid="link-types-sync">
          {t("admin.itsm.linkTypes.sync")}
        </Button>
      </header>
      {list.isPending && <Spinner />}
      {list.isError && (
        <p role="alert" className="text-sm text-danger">
          {t("admin.itsm.error")}
        </p>
      )}
      {sync.isSuccess && (
        <p className="text-sm text-muted" data-testid="link-types-sync-done">
          {t("admin.itsm.linkTypes.synced", { count: sync.data.changed })}
        </p>
      )}
      {list.data && list.data.missing_from_table.length > 0 && (
        <p className="text-sm text-muted" data-testid="link-types-missing">
          {t("admin.itsm.linkTypes.missing", { names: list.data.missing_from_table.join(", ") })}
        </p>
      )}
      {list.data && (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="p-2">{t("admin.itsm.linkTypes.name")}</th>
                <th className="p-2">{t("admin.itsm.linkTypes.origin")}</th>
                <th className="p-2">{t("admin.itsm.linkTypes.labels")}</th>
                {propagationShown && <th className="p-2">{t("admin.itsm.linkTypes.propagation")}</th>}
                <th className="p-2">{t("admin.itsm.linkTypes.offer")}</th>
                <th className="p-2" />
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((row) => (
                <tr key={row.id} className="border-t border-hairline" data-testid={`link-type-row-${row.name}`}>
                  <td className="p-2 font-mono text-xs text-ink">{row.name}</td>
                  <td className="p-2">
                    <Badge tone={row.origin === "znuny" ? "muted" : "accent"} data-testid={`link-type-origin-${row.name}`}>
                      {t(`admin.itsm.linkTypes.origins.${row.origin}`)}
                    </Badge>
                    {!row.valid && (
                      <Badge tone="warn" className="ml-1">
                        {t("admin.itsm.linkTypes.hidden")}
                      </Badge>
                    )}
                  </td>
                  <td className="p-2 text-ink">
                    {[row.source_label, row.target_label].filter(Boolean).join(" / ") || "–"}
                  </td>
                  {propagationShown && (
                    <td className="p-2 text-muted">{t(`admin.itsm.linkTypes.propagations.${row.propagation}`)}</td>
                  )}
                  <td className="p-2 text-xs text-muted">
                    {OFFER_FLAGS.filter((f) => row[f])
                      .map((f) => t(`admin.itsm.linkTypes.flagsShort.${f}`))
                      .join(", ") || "–"}
                  </td>
                  <td className="p-2 text-right">
                    <Button size="sm" onClick={() => setEditing(row)} data-testid={`link-type-edit-${row.name}`}>
                      {t("admin.table.edit")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <EditDialog
          key={editing.id}
          row={editing}
          propagationEditable={propagationShown && editing.origin === "tiqora"}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
