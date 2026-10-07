import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { apiErrorMessage, avatarApi, type AvatarSettings } from "@/lib/avatarApi";

const QUERY_KEY = ["admin", "avatars"] as const;

/**
 * Where customer pictures and company logos may come from automatically:
 * Gravatar (by e-mail hash) and the company website's icon. The server
 * fetches and caches them — browsers never contact these sites. Uploaded
 * pictures always count, whatever is set here.
 */
export function AvatarSettingsDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const settingsQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => avatarApi.getSettings(signal),
  });
  const [draft, setDraft] = useState<AvatarSettings | null>(null);
  useEffect(() => {
    if (!draft && settingsQ.data) setDraft(settingsQ.data);
  }, [draft, settingsQ.data]);

  const saveM = useMutation({
    mutationFn: (body: AvatarSettings) => avatarApi.putSettings(body),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QUERY_KEY });
      onClose();
    },
  });

  const row = (key: keyof AvatarSettings, label: string, hint: string) => (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="min-w-0">
        <p id={`avatar-setting-${key}`} className="text-sm font-medium text-ink">
          {label}
        </p>
        <p className="mt-0.5 text-xs text-muted">{hint}</p>
      </div>
      <Switch
        checked={draft?.[key] ?? false}
        onChange={(on) => setDraft((prev) => (prev ? { ...prev, [key]: on } : prev))}
        aria-labelledby={`avatar-setting-${key}`}
        testId={`admin-avatars-${key}`}
      />
    </div>
  );

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
            disabled={!draft || saveM.isPending}
            onClick={() => draft && saveM.mutate(draft)}
          >
            {saveM.isPending ? <Spinner className="h-4 w-4" /> : t("common.save")}
          </Button>
        </div>
      }
    >
      {settingsQ.isError ? (
        <p className="py-6 text-sm text-danger" data-testid="admin-avatars-load-error">
          {t("admin.avatars.loadError")}
        </p>
      ) : !draft ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : (
        <div className="divide-y divide-hairline" data-testid="admin-avatars-form">
          {row("gravatar_enabled", t("admin.avatars.gravatar"), t("admin.avatars.gravatarHint"))}
          {row("favicon_enabled", t("admin.avatars.favicon"), t("admin.avatars.faviconHint"))}
        </div>
      )}
    </Dialog>
  );
}
