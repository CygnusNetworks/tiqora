import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { ProviderIcon } from "@/components/auth/ProviderIcon";
import { SelectField } from "@/components/ui/SelectField";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  identityApi,
  providerFieldErrors,
  suggestSlug,
  type IdentityProvider,
  type ProviderAudience,
} from "@/lib/identityApi";
import { cn } from "@/lib/cn";

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent";

/** Create a provider from a ready-made template (Microsoft, Google, …). */
export function PresetDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (p: IdentityProvider) => void;
}) {
  const { t } = useTranslation();
  const [presetKey, setPresetKey] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [audience, setAudience] = useState<ProviderAudience>("agent");

  const presetsQ = useQuery({
    queryKey: ["admin", "identity", "presets"],
    enabled: open,
    queryFn: ({ signal }) => identityApi.presets(signal),
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    if (!open) {
      setPresetKey(null);
      setName("");
      setSlug("");
      setSlugTouched(false);
      setAudience("agent");
    }
  }, [open]);

  const createM = useMutation({
    mutationFn: () =>
      identityApi.create({ preset: presetKey as string, name: name.trim(), slug, audience }),
    onSuccess: (created) => onCreated(created),
  });

  if (!open) return null;

  const fieldErrors = providerFieldErrors(createM.error);
  const canSubmit = !!presetKey && name.trim() !== "" && slug !== "" && !createM.isPending;

  const choose = (key: string, audienceDefault: ProviderAudience) => {
    setPresetKey(key);
    setAudience(audienceDefault);
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("admin.identity.presetDialog.title")}
      description={t("admin.identity.presetDialog.description")}
      size="xl"
      footer={
        <>
          <Button type="button" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            type="button"
            variant="primary"
            data-testid="identity-new-submit"
            disabled={!canSubmit}
            onClick={() => createM.mutate()}
          >
            {t("admin.identity.presetDialog.create")}
          </Button>
        </>
      }
    >
      <div className="space-y-4" data-testid="identity-preset-dialog">
        {presetsQ.isLoading ? (
          <Spinner />
        ) : (
          <div role="group" aria-label={t("admin.identity.presetDialog.choose")} className="grid gap-2 sm:grid-cols-2">
            {(presetsQ.data ?? []).map((preset) => {
              const selected = preset.key === presetKey;
              return (
                <button
                  key={preset.key}
                  type="button"
                  data-testid={`identity-preset-${preset.key}`}
                  aria-pressed={selected}
                  onClick={() => choose(preset.key, preset.audience_default as ProviderAudience)}
                  className={cn(
                    "flex items-center gap-3 rounded-lg border px-3 py-2 text-left text-sm",
                    selected ? "border-accent bg-accent-dim text-ink" : "border-hairline bg-surface text-ink hover:border-accent/50",
                  )}
                >
                  <ProviderIcon icon={preset.button_icon} className="h-5 w-5 shrink-0" />
                  <span className="min-w-0">
                    <span className="block font-medium">{t(preset.name_key)}</span>
                    <span className="block text-xs text-muted">{t(`admin.identity.kind.${preset.kind}`)}</span>
                  </span>
                </button>
              );
            })}
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="identity-new-name" className="mb-1 block text-xs font-medium text-muted">
              {t("admin.identity.common.name")}
            </label>
            <input
              id="identity-new-name"
              data-testid="identity-new-name"
              className={inputCls}
              value={name}
              maxLength={120}
              onChange={(e) => {
                setName(e.target.value);
                if (!slugTouched) setSlug(suggestSlug(e.target.value));
              }}
            />
            {fieldErrors.common.name && <p className="mt-1 text-xs text-danger">{fieldErrors.common.name}</p>}
          </div>
          <div>
            <label htmlFor="identity-new-slug" className="mb-1 block text-xs font-medium text-muted">
              {t("admin.identity.common.slug")}
            </label>
            <input
              id="identity-new-slug"
              data-testid="identity-new-slug"
              className={inputCls}
              value={slug}
              maxLength={63}
              onChange={(e) => {
                setSlugTouched(true);
                setSlug(e.target.value);
              }}
            />
            {fieldErrors.common.slug ? (
              <p data-testid="identity-new-slug-error" role="alert" className="mt-1 text-xs text-danger">
                {fieldErrors.common.slug}
              </p>
            ) : (
              <p className="mt-1 text-xs text-muted">{t("admin.identity.common.slugHelp")}</p>
            )}
          </div>
          <div>
            <label htmlFor="identity-new-audience" className="mb-1 block text-xs font-medium text-muted">
              {t("admin.identity.common.audience")}
            </label>
            <SelectField<ProviderAudience>
              id="identity-new-audience"
              testId="identity-new-audience"
              value={audience}
              onChange={setAudience}
              items={(["agent", "customer", "both"] as const).map((a) => ({
                value: a,
                label: t(`admin.identity.audience.${a}`),
              }))}
            />
          </div>
        </div>

        {createM.isError && (
          <p data-testid="identity-new-error" role="alert" className="text-sm text-danger">
            {apiErrorMessage(createM.error, t("admin.identity.presetDialog.createError"))}
          </p>
        )}
      </div>
    </Dialog>
  );
}
