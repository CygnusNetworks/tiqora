import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApproverEntryEditor, type ApproverEntry } from "@/components/approvals/ApproverEntryEditor";
import { changeAdminErrorText, entryToMember, intOrNull, memberToEntry } from "@/components/itsm/change/changeAdminUi";
import { FIELD_CLASS } from "@/components/itsm/change/changeUi";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { changeAdminApi, changeAdminKeys, type AdminCab, type AdminCabIn } from "@/lib/changeAdminApi";
import { changeKeys } from "@/lib/changeApi";

const CAB_RULES: AdminCab["rule"][] = ["any", "all", "majority", "quorum"];
/** The approval engine's subject type of changes: offers the change's own resolvers (`itsm.service_owner`). */
const SUBJECT_TYPE = "itsm.change";
type Problem = "name" | "quorum" | "members";

const LABEL = "mb-1 block text-xs font-medium text-muted";

/**
 * Create or edit a CAB: name, decision rule (with a minimum number for "quorum"), emergency flag and
 * the members. Members use the approval engine's picker (`ApproverEntryEditor`); customer users and
 * customer-yielding rules are offered only while customers may approve (`customersAllowed`, CX21).
 * Board and members are two writes; when a new board was created but its members were refused, the
 * dialog stays open on the created board so the next save only fixes the members.
 */
export function CabDialog({ cab, customersAllowed, onClose }: { cab: AdminCab | null; customersAllowed: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [cabId, setCabId] = useState<number | null>(cab?.id ?? null);
  const [createdOnly, setCreatedOnly] = useState(false);
  const [name, setName] = useState(cab?.name ?? "");
  const [description, setDescription] = useState(cab?.description ?? "");
  const [rule, setRule] = useState<AdminCab["rule"]>(cab?.rule ?? "majority");
  const [quorum, setQuorum] = useState(cab?.quorum != null ? String(cab.quorum) : "");
  const [isEmergency, setIsEmergency] = useState(cab?.is_emergency ?? false);
  const [valid, setValid] = useState(cab?.valid ?? true);
  const [entries, setEntries] = useState<ApproverEntry[]>(() => (cab?.members ?? []).map(memberToEntry));
  const [touched, setTouched] = useState(false);

  const quorumValue = intOrNull(quorum);
  const problems: Problem[] = [];
  if (name.trim() === "") problems.push("name");
  if (rule === "quorum" && !(quorumValue != null && quorumValue >= 1 && quorumValue <= 100)) problems.push("quorum");
  if (entries.some((e) => e.ref.trim() === "")) problems.push("members");

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: changeAdminKeys.cabs() });
    void qc.invalidateQueries({ queryKey: changeKeys.cabs() });
  };
  const save = useMutation({
    mutationFn: async () => {
      const body: AdminCabIn = {
        name: name.trim(),
        description: description.trim() || null,
        rule,
        quorum: rule === "quorum" ? quorumValue : null,
        is_emergency: isEmergency,
        valid,
      };
      let id = cabId;
      if (id == null) {
        id = (await changeAdminApi.createCab(body)).id;
        setCabId(id);
        setCreatedOnly(true);
      } else {
        await changeAdminApi.saveCab(id, body);
      }
      await changeAdminApi.saveCabMembers(id, entries.map(entryToMember));
    },
    onSuccess: () => {
      refresh();
      onClose();
    },
    onError: refresh,
  });
  const submit = () => {
    setTouched(true);
    if (problems.length === 0 && !save.isPending) save.mutate();
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="xl"
      title={cab || cabId != null ? t("admin.itsmChange.cab.edit") : t("admin.itsmChange.cab.new")}
      footer={
        <>
          <Button onClick={onClose} data-testid="cab-cancel">
            {t("common.cancel")}
          </Button>
          <Button variant="primary" disabled={save.isPending} onClick={submit} data-testid="cab-save">
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm" data-testid="cab-dialog">
        <label className="block">
          <span className={LABEL}>{t("admin.itsmChange.cab.name")}</span>
          <input className={FIELD_CLASS} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} aria-invalid={touched && problems.includes("name")} data-testid="cab-name" />
        </label>
        <label className="block">
          <span className={LABEL}>{t("admin.itsmChange.cab.description")}</span>
          <textarea className={FIELD_CLASS} rows={2} maxLength={5000} value={description} onChange={(e) => setDescription(e.target.value)} data-testid="cab-description" />
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="min-w-0">
            <span className={LABEL}>{t("admin.itsmChange.cab.ruleLabel")}</span>
            <SelectField
              items={CAB_RULES.map((r) => ({ value: r, label: t(`admin.itsmChange.cab.rule.${r}`) }))}
              value={rule}
              onChange={setRule}
              testId="cab-rule"
              aria-label={t("admin.itsmChange.cab.ruleLabel")}
            />
          </div>
          {rule === "quorum" && (
            <label className="block min-w-0">
              <span className={LABEL}>{t("admin.itsmChange.cab.quorum")}</span>
              <input type="number" min={1} max={100} className={FIELD_CLASS} value={quorum} onChange={(e) => setQuorum(e.target.value)} aria-invalid={touched && problems.includes("quorum")} data-testid="cab-quorum" />
            </label>
          )}
        </div>
        <label className="flex items-center gap-2 text-ink">
          <Switch checked={isEmergency} onChange={setIsEmergency} testId="cab-emergency-switch" aria-label={t("admin.itsmChange.cab.isEmergency")} />
          {t("admin.itsmChange.cab.isEmergency")}
        </label>
        <label className="flex items-center gap-2 text-ink">
          <Switch checked={valid} onChange={setValid} testId="cab-valid" aria-label={t("admin.itsmChange.valid")} />
          {t("admin.itsmChange.valid")}
        </label>

        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">{t("admin.itsmChange.cab.members")}</h3>
          <p className="text-xs text-muted">{t("admin.itsmChange.cab.membersHint")}</p>
          {!customersAllowed && (
            <p className="text-xs text-muted" data-testid="cab-customers-off">
              {t("admin.itsmChange.cab.customersOff")}
            </p>
          )}
          <ApproverEntryEditor
            value={entries}
            onChange={setEntries}
            subjectType={SUBJECT_TYPE}
            allowCustomers={customersAllowed}
            testIdPrefix="cab-member"
          />
        </div>

        {touched && problems.length > 0 && (
          <ul className="space-y-0.5 text-xs text-danger" role="alert" data-testid="cab-problems">
            {problems.map((p) => (
              <li key={p}>
                {t(
                  p === "name"
                    ? "admin.itsmChange.editor.nameMissing"
                    : p === "quorum"
                      ? "admin.itsmChange.cab.quorumMissing"
                      : "admin.itsmChange.cab.membersIncomplete",
                )}
              </li>
            ))}
          </ul>
        )}
        {save.isError && (
          <p className="text-sm text-danger" role="alert" data-testid="cab-error">
            {createdOnly && <>{t("admin.itsmChange.cab.membersNotSaved")} </>}
            {changeAdminErrorText(save.error, t)}
          </p>
        )}
      </div>
    </Dialog>
  );
}
