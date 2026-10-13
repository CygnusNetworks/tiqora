import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { itsmImportApi } from "@/lib/itsmImportApi";

/** The word the admin has to type; the API checks the same string. */
export const SWITCH_CONFIRM_WORD = "UMSTELLEN";

/**
 * Switch from Mode A (Znuny runs the ITSM) to Tiqora. Collects what the API needs
 * (`addon_installed`, `edits_stopped`, the typed confirmation), starts the switch job and hands
 * its id to the caller, which follows it in a `JobDialog`. `withoutCiData` is the explicit
 * "Umstellen ohne CI-Daten" path for Znuny ITSM releases the importer cannot read.
 */
export function SwitchDialog({
  open,
  onClose,
  onStarted,
  withoutCiData = false,
}: {
  open: boolean;
  onClose: () => void;
  onStarted: (jobId: number) => void;
  withoutCiData?: boolean;
}) {
  const { t } = useTranslation();
  const [addon, setAddon] = useState<boolean | null>(null);
  const [editsStopped, setEditsStopped] = useState(false);
  const [noCiData, setNoCiData] = useState(false);
  const [word, setWord] = useState("");

  const start = useMutation({
    mutationFn: () =>
      itsmImportApi.switchToTiqora({
        confirm: word.trim(),
        addon_installed: addon === true,
        edits_stopped: addon === false ? editsStopped : false,
        without_ci_data: withoutCiData,
      }),
    onSuccess: (accepted) => {
      onStarted(accepted.job_id);
      close();
    },
  });

  const close = () => {
    setAddon(null);
    setEditsStopped(false);
    setNoCiData(false);
    setWord("");
    start.reset();
    onClose();
  };

  const valid =
    addon !== null &&
    (addon || editsStopped) &&
    (!withoutCiData || noCiData) &&
    word.trim() === SWITCH_CONFIRM_WORD;

  return (
    <Dialog
      open={open}
      onClose={close}
      title={t("admin.itsmImport.switch.title")}
      size="lg"
      role="alertdialog"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={start.isPending} data-testid="itsm-switch-cancel">
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => start.mutate()}
            disabled={!valid || start.isPending}
            data-testid="itsm-switch-submit"
          >
            {start.isPending ? <Spinner /> : t("admin.itsmImport.switch.submit")}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm text-ink" data-testid="itsm-switch-dialog">
        <p>{t("admin.itsmImport.switch.intro")}</p>
        <ol className="list-decimal space-y-1 pl-5">
          <li>{t("admin.itsmImport.switch.step1")}</li>
          <li>{t("admin.itsmImport.switch.step2")}</li>
          <li>{t("admin.itsmImport.switch.step3")}</li>
        </ol>

        <fieldset className="space-y-1">
          <legend className="mb-1 font-medium">{t("admin.itsmImport.switch.addonLegend")}</legend>
          <label className="flex items-center gap-2">
            <input type="radio" name="itsm-addon" checked={addon === true} onChange={() => setAddon(true)} />
            {t("admin.itsmImport.switch.addonInstalled")}
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" name="itsm-addon" checked={addon === false} onChange={() => setAddon(false)} />
            {t("admin.itsmImport.switch.addonMissing")}
          </label>
        </fieldset>

        {addon === false && (
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={editsStopped}
              onChange={(e) => setEditsStopped(e.target.checked)}
            />
            {t("admin.itsmImport.switch.editsStopped")}
          </label>
        )}

        {withoutCiData && (
          <div className="space-y-1 rounded-lg border border-warn p-3">
            <p className="text-warn">{t("admin.itsmImport.switch.noCiDataWarning")}</p>
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={noCiData}
                onChange={(e) => setNoCiData(e.target.checked)}
              />
              {t("admin.itsmImport.switch.noCiDataConfirm")}
            </label>
          </div>
        )}

        <label className="block space-y-1">
          <span className="font-medium">{t("admin.itsmImport.switch.typeToConfirm", { word: SWITCH_CONFIRM_WORD })}</span>
          <input
            type="text"
            className="w-full rounded-lg border border-hairline bg-surface px-2 py-1.5"
            value={word}
            onChange={(e) => setWord(e.target.value)}
            autoComplete="off"
            data-testid="itsm-switch-confirm"
          />
        </label>

        {start.isError && (
          <p className="text-danger" role="alert" data-testid="itsm-switch-error">
            {apiErrorMessage(start.error, t("admin.itsmImport.switch.error"))}
          </p>
        )}
      </div>
    </Dialog>
  );
}
