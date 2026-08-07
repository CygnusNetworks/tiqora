import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { SelectMenu } from "@/components/ui/SelectMenu";
import { localePickerItems } from "@/i18n";
import { api, ApiError, type UserOut } from "@/lib/api";

/**
 * Admin-editable mirror of the agent's own "Persönliche Einstellungen" page
 * (currently just UI language — the only preference persisted server-side;
 * theme is client-only `localStorage` and has no admin-editable equivalent).
 */
export function AgentSettingsDialog({
  user,
  onClose,
}: {
  user: UserOut | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const open = user !== null;
  const languageItems = localePickerItems({ all: true });

  const langQ = useQuery({
    queryKey: ["admin", "users", user?.id, "language"],
    queryFn: ({ signal }) => api.getUserLanguage(user!.id, signal),
    enabled: open,
  });

  const [language, setLanguage] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [savedFlash, setSavedFlash] = useState(false);

  useEffect(() => {
    if (open) {
      setLanguage(langQ.data?.language ?? null);
      setSubmitError(null);
      setSavedFlash(false);
    }
  }, [open, langQ.data]);

  const saveMut = useMutation({
    mutationFn: (code: string) => api.setUserLanguage(user!.id, { language: code }),
    onSuccess: async () => {
      setSubmitError(null);
      setSavedFlash(true);
      window.setTimeout(() => setSavedFlash(false), 1500);
      await qc.invalidateQueries({ queryKey: ["admin", "users", user?.id, "language"] });
    },
    onError: (err: unknown) => {
      setSubmitError(err instanceof ApiError ? err.message : String(err));
    },
  });

  const name = user ? `${user.first_name} ${user.last_name}`.trim() || user.login : "";
  const selectedLabel = languageItems.find((l) => l.value === language)?.label ?? language;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("admin.users.editSettingsTitle", { name })}
      description={t("admin.users.editSettingsDescription")}
      size="sm"
      footer={
        <>
          {submitError && (
            <p className="mr-auto text-sm text-escalation" data-testid="agent-settings-error">
              {submitError}
            </p>
          )}
          {savedFlash && !submitError && (
            <p className="mr-auto text-sm text-green">{t("admin.users.languageSaved")}</p>
          )}
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            disabled={!language || saveMut.isPending}
            data-testid="agent-settings-save"
            onClick={() => language && saveMut.mutate(language)}
          >
            {saveMut.isPending ? t("admin.form.saving") : t("common.save")}
          </Button>
        </>
      }
    >
      {langQ.isLoading ? (
        <div className="flex justify-center py-6">
          <Spinner />
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium uppercase tracking-wide text-muted">
            {t("admin.users.languageLabel")}
          </label>
          <SelectMenu
            items={languageItems}
            value={language}
            onSelect={(v) => setLanguage(v)}
            panelTestId="agent-settings-lang-panel"
            trigger={({ open: menuOpen, ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="agent-settings-lang-select"
                {...toggleProps}
                className="flex w-full items-center justify-between rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-left text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              >
                <span className={selectedLabel ? "truncate" : "truncate text-muted"}>
                  {selectedLabel ?? t("admin.form.selectPlaceholder")}
                </span>
                <span className={menuOpen ? "rotate-180 text-muted" : "text-muted"} aria-hidden>
                  ⌄
                </span>
              </button>
            )}
          />
        </div>
      )}
    </Dialog>
  );
}
