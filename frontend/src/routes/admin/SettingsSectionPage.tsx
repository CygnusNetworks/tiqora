import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ensurePluginI18n } from "@/plugins/i18n";
import { usePlugins } from "@/plugins/PluginProvider";
import { SchemaForm } from "@/components/fields/SchemaForm";
import type { FieldErrors, FieldValues } from "@/components/fields/types";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError } from "@/lib/api";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  changedSectionValues,
  initialSectionValues,
  settingsFieldErrors,
  settingsSectionsApi,
} from "@/lib/settingsSectionsApi";

/** Generic admin page for one settings section, generated from its JSON schema. */
export function SettingsSectionPage() {
  const { t, i18n } = useTranslation();
  const { section: key } = useParams({ strict: false }) as { section: string };
  const qc = useQueryClient();
  const sectionQ = useQuery({
    queryKey: ["settings-sections", key],
    queryFn: ({ signal }) => settingsSectionsApi.get(key, signal),
    retry: false,
  });
  const [draft, setDraft] = useState<FieldValues | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [saved, setSaved] = useState(false);

  // Plugin sections (`plugins.<id>`) take their labels from the plugin's own
  // translations: show the form only once they are loaded. A plugin missing
  // from the list (or safe mode) renders with the schema titles instead.
  const { plugins, ready: pluginsReady } = usePlugins();
  const pluginId = key.startsWith("plugins.") ? key.slice("plugins.".length) : null;
  const plugin = pluginId ? plugins.find((p) => p.id === pluginId) : undefined;
  const [textsFor, setTextsFor] = useState<string | null>(null);
  useEffect(() => {
    if (!pluginId || !pluginsReady) return;
    if (!plugin) {
      setTextsFor(key);
      return;
    }
    let active = true;
    void ensurePluginI18n(plugin, i18n.language).then(() => {
      if (active) setTextsFor(key);
    });
    return () => {
      active = false;
    };
  }, [key, pluginId, plugin, pluginsReady, i18n.language]);
  const waitingForTexts = pluginId !== null && textsFor !== key;

  useEffect(() => {
    if (sectionQ.data) setDraft(initialSectionValues(sectionQ.data));
  }, [sectionQ.data]);

  const saveM = useMutation({
    mutationFn: (values: Record<string, unknown>) => settingsSectionsApi.put(key, values),
    onSuccess: async (updated) => {
      setErrors({});
      setSaved(true);
      qc.setQueryData(["settings-sections", key], updated);
      await qc.invalidateQueries({ queryKey: ["settings-sections"] });
    },
    onError: (err) => {
      setSaved(false);
      setErrors(settingsFieldErrors(err));
    },
  });

  if (sectionQ.isError) {
    const notFound = sectionQ.error instanceof ApiError && sectionQ.error.status === 404;
    return (
      <div className="p-4 text-sm text-danger" data-testid={notFound ? "admin-settings-not-found" : "admin-settings-load-error"}>
        {notFound ? t("admin.settings.notFound") : t("admin.settings.loadError")}
      </div>
    );
  }
  if (!sectionQ.data || !draft || waitingForTexts) {
    return (
      <div className="flex justify-center p-8" data-testid="admin-settings-page">
        <Spinner />
      </div>
    );
  }

  const section = sectionQ.data;
  const changes = changedSectionValues(section, draft);
  const hasFieldErrors = Object.keys(errors).length > 0;

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4" data-testid="admin-settings-page">
      <h1 className="font-display text-xl font-semibold text-ink" data-testid="admin-settings-title">
        {t(section.title_key)}
      </h1>
      <div className="rounded-xl border border-hairline bg-surface p-4">
        <SchemaForm
          schema={section.json_schema}
          values={draft}
          onChange={(next, changedKey) => {
            setDraft(next);
            setSaved(false);
            setErrors((prev) => {
              if (!(changedKey in prev)) return prev;
              const rest = { ...prev };
              delete rest[changedKey];
              return rest;
            });
          }}
          errors={errors}
          i18nPrefix={section.i18n_prefix}
          secretsSet={section.secrets_set}
          disabled={saveM.isPending}
        />
      </div>
      <div className="flex items-center justify-end gap-3">
        {saved && (
          <p className="mr-auto text-xs text-green" data-testid="admin-settings-saved">
            {t("admin.settings.saved")}
          </p>
        )}
        {saveM.isError && (
          <p className="mr-auto text-xs text-danger" data-testid="admin-settings-error">
            {hasFieldErrors ? t("admin.settings.saveError") : apiErrorMessage(saveM.error, t("admin.settings.saveError"))}
          </p>
        )}
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-settings-save"
          disabled={saveM.isPending || Object.keys(changes).length === 0}
          onClick={() => saveM.mutate(changes)}
        >
          {saveM.isPending ? <Spinner className="h-4 w-4" /> : t("admin.settings.save")}
        </Button>
      </div>
    </div>
  );
}
