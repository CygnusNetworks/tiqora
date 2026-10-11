import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { DashboardConflictError, type DashboardTab } from "@/lib/dashboardsApi";

export const NAME_MAX = 120;

const INPUT =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/** The server's German message, the conflict text, or `fallback`. */
function failureText(err: unknown, fallback: string, conflict: string): string {
  if (err instanceof DashboardConflictError) return conflict;
  return apiErrorMessage(err, fallback);
}

function NameDialog({
  title,
  submitLabel,
  initialName,
  copyOptions,
  fallbackError,
  onSubmit,
  onClose,
}: {
  title: string;
  submitLabel: string;
  initialName: string;
  /** Dashboards the new one can start as a copy of; omitted for a rename. */
  copyOptions?: DashboardTab[];
  fallbackError: string;
  onSubmit: (name: string, copyFrom: number | null) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const ids = useId();
  const [name, setName] = useState(initialName);
  const [copyFrom, setCopyFrom] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (pending) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError(t("dashboards.manage.nameRequired"));
      return;
    }
    setNameError(null);
    setError(null);
    setPending(true);
    try {
      await onSubmit(trimmed, copyFrom === "" ? null : Number(copyFrom));
    } catch (err) {
      setError(failureText(err, fallbackError, t("dashboards.manage.conflict")));
      setPending(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={pending}>
            {t("dashboards.manage.cancel")}
          </Button>
          <Button variant="primary" onClick={() => void submit()} disabled={pending}>
            {pending ? <Spinner /> : submitLabel}
          </Button>
        </>
      }
    >
      <form onSubmit={(e) => void submit(e)} className="space-y-4" noValidate>
        <div className="space-y-1.5">
          <label htmlFor={`${ids}-name`} className="text-[13px] font-medium text-ink">
            {t("dashboards.manage.name")}
          </label>
          <input
            id={`${ids}-name`}
            type="text"
            value={name}
            maxLength={NAME_MAX}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={nameError ? true : undefined}
            aria-describedby={nameError ? `${ids}-name-err` : undefined}
            className={INPUT}
          />
          {nameError && (
            <p id={`${ids}-name-err`} className="text-xs text-danger">
              {nameError}
            </p>
          )}
        </div>
        {copyOptions && (
          <div className="space-y-1.5">
            <label htmlFor={`${ids}-copy`} className="text-[13px] font-medium text-ink">
              {t("dashboards.manage.copyFrom")}
            </label>
            <select id={`${ids}-copy`} value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)} className={INPUT}>
              <option value="">{t("dashboards.manage.copyNone")}</option>
              {copyOptions.map((tab) => (
                <option key={tab.id} value={tab.id}>
                  {tab.name}
                </option>
              ))}
            </select>
          </div>
        )}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        {/* Enter in the name field submits (the footer button sits outside the form). */}
        <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
      </form>
    </Dialog>
  );
}

/** "+" dialog: a required name (trimmed, up to 120 characters) and an optional "Kopie von ...". */
export function NewDashboardDialog({
  dashboards = [],
  onCreate,
  onClose,
}: {
  /** The visible dashboards offered under "Kopie von". */
  dashboards?: DashboardTab[];
  onCreate: (name: string, copyFrom?: number | null) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <NameDialog
      title={t("dashboards.manage.newTitle")}
      submitLabel={t("dashboards.manage.create")}
      initialName=""
      copyOptions={dashboards}
      fallbackError={t("dashboards.manage.createFailed")}
      onSubmit={(name, copyFrom) => onCreate(name, copyFrom)}
      onClose={onClose}
    />
  );
}

/** Rename dialog of the tab menu; a 409 keeps the dialog open and says why. */
export function RenameDashboardDialog({
  name,
  onRename,
  onClose,
}: {
  name: string;
  onRename: (name: string) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <NameDialog
      title={t("dashboards.manage.renameTitle")}
      submitLabel={t("dashboards.manage.save")}
      initialName={name}
      fallbackError={t("dashboards.manage.renameFailed")}
      onSubmit={(next) => onRename(next)}
      onClose={onClose}
    />
  );
}
