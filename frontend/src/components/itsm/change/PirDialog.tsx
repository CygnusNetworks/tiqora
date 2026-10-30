import { useState } from "react";
import { useTranslation } from "react-i18next";
import { SelectField } from "@/components/ui/SelectField";
import type { Change, ChangeOutcome } from "@/lib/changeApi";
import { FAILED_OUTCOMES, FIELD_CLASS } from "./changeUi";
import { TransitionDialog } from "./TransitionDialog";

const OUTCOMES: readonly ChangeOutcome[] = ["successful", "failed", "backed_out"];

/**
 * The steps that record the post-implementation review: "Zur Nachbetrachtung" (`review`, notes
 * optional) and "Abschließen" (`closed`, the outcome is required; a normal or emergency change
 * closed from the review also needs the notes, already stored or written here).
 */
export function PirDialog({
  change,
  to,
  onClose,
  onDone,
}: {
  change: Change;
  to: "review" | "closed";
  onClose: () => void;
  onDone: (change: Change) => void;
}) {
  const { t } = useTranslation();
  const [outcome, setOutcome] = useState<ChangeOutcome | null>(null);
  const [notes, setNotes] = useState(change.pir_notes ?? "");
  const closing = to === "closed";
  const notesRequired = closing && change.phase === "review" && change.change_type !== "standard" && !notes.trim();

  const body = {
    ...(closing && outcome ? { outcome } : {}),
    ...(notes.trim() ? { pir_notes: notes.trim() } : {}),
  };

  return (
    <TransitionDialog
      change={change}
      to={to}
      onClose={onClose}
      onDone={onDone}
      title={closing ? t("itsm.change.pir.closeTitle") : t("itsm.change.pir.reviewTitle")}
      body={body}
      submitDisabled={(closing && !outcome) || notesRequired}
      extra={
        <div className="space-y-3" data-testid="change-pir-dialog">
          {closing && (
            <div className="space-y-1">
              <label htmlFor="change-pir-outcome" className="block text-xs text-muted">
                {t("itsm.change.pir.outcome")}
              </label>
              <SelectField<ChangeOutcome>
                id="change-pir-outcome"
                testId="change-pir-outcome"
                items={OUTCOMES.map((o) => ({ value: o, label: t(`itsm.change.pir.outcomes.${o}`) }))}
                value={outcome}
                onChange={setOutcome}
                placeholder={t("itsm.change.pir.pickOutcome")}
              />
            </div>
          )}
          {closing && outcome && FAILED_OUTCOMES.has(outcome) && (
            <p className="text-xs text-muted" data-testid="change-pir-incident-hint">
              {t("itsm.change.incident.afterClose")}
            </p>
          )}
          <label className="block text-xs text-muted">
            {t("itsm.change.pir.notes")}
            <textarea
              className={`${FIELD_CLASS} mt-1`}
              rows={5}
              maxLength={20000}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              data-testid="change-pir-notes"
            />
          </label>
          {notesRequired && (
            <p className="text-xs text-danger" data-testid="change-pir-notes-required">
              {t("itsm.change.pir.notesRequired")}
            </p>
          )}
        </div>
      }
    />
  );
}
