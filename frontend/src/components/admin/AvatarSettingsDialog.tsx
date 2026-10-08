import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { SchemaForm } from "@/components/fields/SchemaForm";
import type { FieldValues } from "@/components/fields/types";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  changedSectionValues,
  initialSectionValues,
  settingsFieldErrors,
  settingsSectionsApi,
} from "@/lib/settingsSectionsApi";

const QUERY_KEY = ["admin", "avatars"] as const;
const SECTION_KEY = ["settings-sections", "avatars"] as const;

/**
 * Where customer pictures and company logos may come from automatically:
 * Gravatar (by e-mail hash) and the company website's icon. The server
 * fetches and caches them — browsers never contact these sites. Uploaded
 * pictures always count, whatever is set here.
 */
export function AvatarSettingsDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const sectionQ = useQuery({
    queryKey: SECTION_KEY,
    queryFn: ({ signal }) => settingsSectionsApi.get("avatars", signal),
  });
  const [draft, setDraft] = useState<FieldValues | null>(null);
  useEffect(() => {
    if (!draft && sectionQ.data) setDraft(initialSectionValues(sectionQ.data));
  }, [draft, sectionQ.data]);

  const saveM = useMutation({
    mutationFn: (values: Record<string, unknown>) => settingsSectionsApi.put("avatars", values),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: QUERY_KEY }),
        qc.invalidateQueries({ queryKey: SECTION_KEY }),
      ]);
      onClose();
    },
  });
  const section = sectionQ.data;

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("admin.avatars.title")}
      description={t("admin.avatars.hint")}
      size="md"
      footer={
        <div className="flex items-center justify-end gap-2">
          {saveM.isError && (
            <p className="mr-auto text-xs text-danger" data-testid="admin-avatars-error">
              {apiErrorMessage(saveM.error, t("admin.form.genericError"))}
            </p>
          )}
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saveM.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            data-testid="admin-avatars-save"
            disabled={!section || !draft || saveM.isPending}
            onClick={() => section && draft && saveM.mutate(changedSectionValues(section, draft))}
          >
            {saveM.isPending ? <Spinner className="h-4 w-4" /> : t("common.save")}
          </Button>
        </div>
      }
    >
      {sectionQ.isError ? (
        <p className="py-6 text-sm text-danger" data-testid="admin-avatars-load-error">
          {t("admin.avatars.loadError")}
        </p>
      ) : !section || !draft ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : (
        <SchemaForm
          schema={section.json_schema}
          values={draft}
          onChange={(next) => setDraft(next)}
          errors={settingsFieldErrors(saveM.error)}
          i18nPrefix={section.i18n_prefix}
          secretsSet={section.secrets_set}
          disabled={saveM.isPending}
          testId="admin-avatars"
        />
      )}
    </Dialog>
  );
}
