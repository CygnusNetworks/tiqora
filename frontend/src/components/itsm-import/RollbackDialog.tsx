import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { itsmImportApi, itsmImportKeys } from "@/lib/itsmImportApi";

/** The word the admin has to type; the API checks the same string. */
export const ROLLBACK_CONFIRM_WORD = "ZURÜCK";
/** Accepted as well, for keyboards without Ü (the API accepts it too). */
export const ROLLBACK_CONFIRM_ASCII = "ZURUECK";

/** The typed word as the API compares it: NFC (a pasted decomposed Ü), trimmed, ASCII spelling mapped. */
function normaliseConfirmWord(raw: string): string {
  const word = raw.normalize("NFC").trim();
  return word === ROLLBACK_CONFIRM_ASCII ? ROLLBACK_CONFIRM_WORD : word;
}

/** Back from Tiqora to Znuny (Mode A). Refreshes the import status and the run list on success. */
export function RollbackDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [word, setWord] = useState("");

  const rollback = useMutation({
    mutationFn: () => itsmImportApi.rollback({ confirm: normaliseConfirmWord(word) }),
    onSuccess: async (status) => {
      qc.setQueryData(itsmImportKeys.status(), status);
      await qc.invalidateQueries({ queryKey: itsmImportKeys.status() });
      await qc.invalidateQueries({ queryKey: itsmImportKeys.runs() });
      close();
    },
  });

  const close = () => {
    setWord("");
    rollback.reset();
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title={t("admin.itsmImport.rollback.title")}
      role="alertdialog"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={rollback.isPending} data-testid="itsm-rollback-cancel">
            {t("common.cancel")}
          </Button>
          <Button
            variant="danger"
            size="sm"
            onClick={() => rollback.mutate()}
            disabled={normaliseConfirmWord(word) !== ROLLBACK_CONFIRM_WORD || rollback.isPending}
            data-testid="itsm-rollback-submit"
          >
            {rollback.isPending ? <Spinner /> : t("admin.itsmImport.rollback.submit")}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm text-ink" data-testid="itsm-rollback-dialog">
        <p>{t("admin.itsmImport.rollback.message")}</p>
        <label className="block space-y-1">
          <span className="font-medium">{t("admin.itsmImport.rollback.typeToConfirm", { word: ROLLBACK_CONFIRM_WORD })}</span>
          <input
            type="text"
            className="w-full rounded-lg border border-hairline bg-surface px-2 py-1.5"
            value={word}
            onChange={(e) => setWord(e.target.value)}
            autoComplete="off"
            data-testid="itsm-rollback-confirm"
          />
        </label>
        {rollback.isError && (
          <p className="text-danger" role="alert" data-testid="itsm-rollback-error">
            {apiErrorMessage(rollback.error, t("admin.itsmImport.rollback.error"))}
          </p>
        )}
      </div>
    </Dialog>
  );
}
