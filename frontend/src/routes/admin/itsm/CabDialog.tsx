import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { ApproverEntryEditor, type ApproverEntry } from "@/components/approvals/ApproverEntryEditor";
import { changeAdminErrorText, entryToMember, intOrNull, memberToEntry } from "@/components/itsm/change/changeAdminUi";
import { FIELD_CLASS, LABEL_CLASS } from "@/components/itsm/change/changeUi";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { changeAdminApi, changeAdminKeys, type AdminCab, type AdminCabIn } from "@/lib/changeAdminApi";
import { changeErrorCode, changeErrorDetail, changeKeys } from "@/lib/changeApi";

const CAB_RULES: AdminCab["rule"][] = ["any", "all", "majority", "quorum"];
/** The approval engine's subject type of changes: offers the change's own resolvers (`itsm.service_owner`). */
const SUBJECT_TYPE = "itsm.change";
type Problem = "name" | "quorum" | "quorumTooLarge" | "members";
/** Kinds the engine counts one by one; groups, roles, lists and rules expand later. */
const COUNTED_KINDS: ReadonlySet<ApproverEntry["kind"]> = new Set(["user", "customer_user"]);

/**
 * The engine's own quorum check (`approvals.rules.validate_definition`): with only individual members,
 * the quorum cannot exceed the number of distinct members.
 */
function quorumExceedsMembers(quorum: number | null, entries: ApproverEntry[]): boolean {
  if (quorum == null || entries.length === 0 || !entries.every((e) => COUNTED_KINDS.has(e.kind))) return false;
  return quorum > new Set(entries.map((e) => `${e.kind}:${e.ref}`)).size;
}

/** `cab_invalid` names its reason only in an English engine message; the quorum case gets its own text. */
function cabErrorText(err: unknown, t: TFunction): string {
  if (changeErrorCode(err) !== "cab_invalid") return changeAdminErrorText(err, t);
  const message = changeErrorDetail(err)?.message;
  return typeof message === "string" && /quorum \d+ exceeds/.test(message)
    ? t("admin.itsmChange.cab.quorumTooLarge")
    : t("admin.itsmChange.cab.ruleMismatch");
}

/**
 * Create or edit a CAB: name, decision rule (with a minimum number for "quorum"), emergency flag and
 * the members. Members use the approval engine's picker (`ApproverEntryEditor`); customer users and
 * customer-yielding rules are offered only while customers may approve (`customersAllowed`, CX21).
 * Board and members are two writes, each checked by the backend against the other half as stored.
 * The board goes first; when the new rule is refused (`cab_invalid`) because it needs the new members
 * (a quorum raised together with added members), the members go first and the board follows.
 * Unchanged members are not written. When only one half got saved, the dialog stays open and says so.
 */
export function CabDialog({ cab, customersAllowed, onClose }: { cab: AdminCab | null; customersAllowed: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [cabId, setCabId] = useState<number | null>(cab?.id ?? null);
  const [partial, setPartial] = useState<"created" | "board" | null>(null);
  const [storedMembers, setStoredMembers] = useState(() => JSON.stringify((cab?.members ?? []).map(memberToEntry)));
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
  else if (rule === "quorum" && quorumExceedsMembers(quorumValue, entries)) problems.push("quorumTooLarge");
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
      setPartial(null);
      const members = entries.map(entryToMember);
      const membersChanged = JSON.stringify(entries) !== storedMembers;
      const saveMembers = async (id: number) => {
        await changeAdminApi.saveCabMembers(id, members);
        setStoredMembers(JSON.stringify(entries));
      };
      if (cabId == null) {
        const id = (await changeAdminApi.createCab(body)).id;
        setCabId(id);
        setStoredMembers("[]");
        if (members.length === 0) return;
        setPartial("created");
        await saveMembers(id);
        return;
      }
      try {
        await changeAdminApi.saveCab(cabId, body);
      } catch (err) {
        if (!membersChanged || changeErrorCode(err) !== "cab_invalid") throw err;
        await saveMembers(cabId);
        await changeAdminApi.saveCab(cabId, body);
        return;
      }
      if (!membersChanged) return;
      setPartial("board");
      await saveMembers(cabId);
    },
    onSuccess: () => {
      setPartial(null);
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
          <span className={LABEL_CLASS}>{t("admin.itsmChange.cab.name")}</span>
          <input className={FIELD_CLASS} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} aria-invalid={touched && problems.includes("name")} data-testid="cab-name" />
        </label>
        <label className="block">
          <span className={LABEL_CLASS}>{t("admin.itsmChange.cab.description")}</span>
          <textarea className={FIELD_CLASS} rows={2} maxLength={5000} value={description} onChange={(e) => setDescription(e.target.value)} data-testid="cab-description" />
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="min-w-0">
            <span className={LABEL_CLASS}>{t("admin.itsmChange.cab.ruleLabel")}</span>
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
              <span className={LABEL_CLASS}>{t("admin.itsmChange.cab.quorum")}</span>
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
                      : p === "quorumTooLarge"
                        ? "admin.itsmChange.cab.quorumTooLarge"
                        : "admin.itsmChange.cab.membersIncomplete",
                )}
              </li>
            ))}
          </ul>
        )}
        {save.isError && (
          <p className="text-sm text-danger" role="alert" data-testid="cab-error">
            {partial && <>{t(partial === "created" ? "admin.itsmChange.cab.membersNotSaved" : "admin.itsmChange.cab.boardSavedOnly")} </>}
            {cabErrorText(save.error, t)}
          </p>
        )}
      </div>
    </Dialog>
  );
}
