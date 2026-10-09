import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ProviderIcon } from "@/components/auth/ProviderIcon";
import { FieldsForm } from "@/components/fields/FieldsForm";
import { schemaDefaults, schemaToFieldDefs, secretKeys, type JsonSchema } from "@/components/fields/jsonSchema";
import { registerCoreFieldKinds } from "@/components/fields/kinds";
import type { FieldDef, FieldErrors, FieldValue, FieldValues } from "@/components/fields/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  identityApi,
  providerFieldErrors,
  type IdentityCapabilities,
  type IdentityKind,
  type IdentityProvider,
  type ProviderChanges,
} from "@/lib/identityApi";
import { CopyButton } from "./CopyButton";
import { EndpointsBox } from "./EndpointsBox";

registerCoreFieldKinds();

export const ICONS = ["microsoft", "google", "key", "shield", "building", "directory"] as const;

const COMMON_KEYS = ["name", "slug", "audience", "enabled", "button_label", "sort"] as const;

function commonValues(p: IdentityProvider): FieldValues {
  return {
    name: p.name,
    slug: p.slug,
    audience: p.audience,
    enabled: p.enabled,
    button_label: p.button_label ?? "",
    button_icon: p.button_icon ?? "",
    sort: p.sort,
  };
}

function configValues(p: IdentityProvider, schema: JsonSchema, secrets: Set<string>): FieldValues {
  const defaults = schemaDefaults(schema);
  const out: FieldValues = {};
  for (const key of Object.keys(schema.properties ?? {})) {
    if (secrets.has(key)) {
      out[key] = null; // null = untouched, "" = remove, text = replace
      continue;
    }
    const stored = (p.config as Record<string, unknown>)[key];
    out[key] = (stored === undefined || stored === null ? defaults[key] : stored) as FieldValue;
  }
  return out;
}

/** The connection tab: common fields, the kind's generated config form, and the addresses to enter at the IdP. */
export function ConnectionTab({
  provider,
  kinds,
  capabilities,
  onSaved,
  onDraftChange,
}: {
  provider: IdentityProvider;
  kinds: IdentityKind[];
  capabilities: IdentityCapabilities;
  onSaved: (p: IdentityProvider) => void;
  onDraftChange?: (draft: ProviderChanges | undefined) => void;
}) {
  const { t } = useTranslation();
  const kind = kinds.find((k) => k.kind === provider.kind);
  const rawSchema = kind?.json_schema;
  const schema = useMemo(() => (rawSchema ?? { properties: {} }) as JsonSchema, [rawSchema]);
  const prefix = kind?.i18n_prefix ?? `admin.identity.config.${provider.kind}`;
  const locked = provider.read_only;

  const [latest, setLatest] = useState(provider);
  const secretKeySet = useMemo(() => new Set(secretKeys(schema)), [schema]);
  const [baseline, setBaseline] = useState(() => ({
    common: commonValues(provider),
    config: configValues(provider, schema, secretKeySet),
  }));
  const [common, setCommon] = useState<FieldValues>(baseline.common);
  const [config, setConfig] = useState<FieldValues>(baseline.config);
  const [saved, setSaved] = useState(false);
  const [metaError, setMetaError] = useState<string | null>(provider.metadata_error);

  const { fields, unsupported } = useMemo(() => {
    const built = schemaToFieldDefs(schema, { t, i18nPrefix: prefix, secretsSet: latest.secrets_set });
    const hidden = new Set(kind?.hidden_fields ?? []);
    const readOnly = new Set(kind?.read_only_fields ?? []);
    const list = built.fields
      .filter((f) => !hidden.has(f.key))
      .map<FieldDef>((f) => (readOnly.has(f.key) ? { ...f, readOnly: true } : f));
    return { fields: list, unsupported: built.unsupported.filter((k) => !hidden.has(k)) };
  }, [schema, t, prefix, latest.secrets_set, kind]);

  const commonFields = useMemo<FieldDef[]>(
    () => [
      { key: "name", kind: "text", label: t("admin.identity.common.name"), required: true, maxLength: 120 },
      { key: "slug", kind: "text", label: t("admin.identity.common.slug"), help: t("admin.identity.common.slugHelp"), required: true, maxLength: 63 },
      {
        key: "audience",
        kind: "select",
        label: t("admin.identity.common.audience"),
        options: (["agent", "customer", "both"] as const).map((a) => ({ value: a, label: t(`admin.identity.audience.${a}`) })),
      },
      { key: "enabled", kind: "boolean", label: t("admin.identity.common.enabled"), help: t("admin.identity.common.enabledHelp") },
      { key: "button_label", kind: "text", label: t("admin.identity.common.buttonLabel"), help: t("admin.identity.common.buttonLabelHelp"), maxLength: 80 },
      { key: "sort", kind: "number", label: t("admin.identity.common.sort"), help: t("admin.identity.common.sortHelp"), min: 0, step: 1 },
    ],
    [t],
  );

  const changes = useMemo<ProviderChanges>(() => {
    const out: ProviderChanges = {};
    const top = out as Record<string, unknown>;
    for (const key of [...COMMON_KEYS, "button_icon"] as const) {
      if (common[key] !== baseline.common[key]) top[key] = common[key];
    }
    const cfg: Record<string, unknown> = {};
    for (const f of fields) {
      if (f.readOnly) continue;
      const value = config[f.key];
      if (f.kind === "secret") {
        if (value !== null && value !== undefined) cfg[f.key] = value;
      } else if (value !== baseline.config[f.key]) {
        cfg[f.key] = value;
      }
    }
    if (Object.keys(cfg).length > 0) out.config = cfg;
    return out;
  }, [common, config, baseline, fields]);
  const dirty = Object.keys(changes).length > 0;

  const onDraft = useRef(onDraftChange);
  useEffect(() => {
    onDraft.current = onDraftChange;
  });
  const changesKey = JSON.stringify(changes);
  useEffect(() => {
    onDraft.current?.(dirty ? changes : undefined);
    // changesKey stands for `changes`; `dirty` follows from it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changesKey]);

  const apply = (p: IdentityProvider) => {
    const fresh = { common: commonValues(p), config: configValues(p, schema, secretKeySet) };
    setLatest(p);
    setBaseline(fresh);
    setCommon(fresh.common);
    setConfig(fresh.config);
    setMetaError(p.metadata_error);
  };

  // A refetched provider replaces the form while nothing is being edited.
  const seen = useRef(provider);
  useEffect(() => {
    if (seen.current === provider) return;
    seen.current = provider;
    if (!dirty) apply(provider);
    // apply/dirty are read at the moment the prop changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const saveM = useMutation({
    mutationFn: () => identityApi.update(provider.id, changes),
    onSuccess: (p) => {
      apply(p);
      setSaved(true);
      onSaved(p);
    },
  });

  const refreshM = useMutation({
    mutationFn: () => identityApi.refreshMetadata(provider.id),
    onSuccess: (p) => {
      setLatest(p);
      setMetaError(p.metadata_error);
      onSaved(p);
    },
    onError: (err) => setMetaError(apiErrorMessage(err, t("admin.identity.saml.refreshError"))),
  });

  const fieldErrors = providerFieldErrors(saveM.error);
  const shownConfig: FieldValues = { ...config };
  for (const f of fields) {
    if (f.readOnly) shownConfig[f.key] = ((latest.config as Record<string, unknown>)[f.key] as FieldValue) ?? "";
  }

  const samlUnavailable = provider.kind === "saml" && !capabilities.saml_available;
  const certificate = provider.kind === "saml" ? String((latest.config as Record<string, unknown>).sp_certificate ?? "") : "";
  const iconValue = String(common.button_icon ?? "");
  // Environment-configured LDAP shows its TIQORA_LDAP_TLS_VERIFY value here (off unless set).
  const ldapTlsUnchecked =
    provider.kind === "ldap" && shownConfig.tls_verify === false && (!!shownConfig.use_ssl || !!shownConfig.use_starttls);

  return (
    <div className="space-y-4" data-testid="identity-connection">
      {locked && (
        <p
          data-testid="identity-env-badge"
          className="rounded border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink"
        >
          {t("admin.identity.envBadge")}
        </p>
      )}
      {samlUnavailable && (
        <p
          data-testid="identity-saml-unavailable"
          role="alert"
          className="rounded border border-escalation/30 bg-escalation/15 px-3 py-2 text-sm text-ink"
        >
          {t(`admin.identity.saml.unavailable.${capabilities.saml_missing ?? "package"}`)}
        </p>
      )}

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.sections.general")}</h2>
        <FieldsForm
          fields={commonFields}
          values={common}
          onChange={(key, value) => setCommon((c) => ({ ...c, [key]: value }))}
          errors={fieldErrors.common as FieldErrors}
          disabled={locked}
          columns={2}
          testId="identity-common"
        />
        <div>
          <div id="identity-icon-label" className="mb-1 text-xs font-medium text-muted">
            {t("admin.identity.common.buttonIcon")}
          </div>
          <div role="group" aria-labelledby="identity-icon-label" className="flex flex-wrap gap-2">
            {ICONS.map((icon) => (
              <button
                key={icon}
                type="button"
                disabled={locked}
                data-testid={`identity-icon-${icon}`}
                aria-pressed={iconValue === icon}
                onClick={() => setCommon((c) => ({ ...c, button_icon: icon }))}
                className={cn(
                  "flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[12.5px] disabled:opacity-60",
                  iconValue === icon ? "border-accent bg-accent-dim text-ink" : "border-hairline bg-surface text-muted hover:text-ink",
                )}
              >
                <ProviderIcon icon={icon} className="h-4 w-4 shrink-0" />
                {t(`admin.identity.icons.${icon}`)}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-ink">{t("admin.identity.sections.connection")}</h2>
          <Badge tone="muted">{t(`admin.identity.kind.${provider.kind}`)}</Badge>
        </div>
        <FieldsForm
          fields={fields}
          values={shownConfig}
          onChange={(key, value) => setConfig((c) => ({ ...c, [key]: value }))}
          errors={fieldErrors.config as FieldErrors}
          disabled={locked}
          columns={2}
          testId="identity-config"
        />
        {unsupported.map((key) => (
          <p key={key} className="text-xs text-muted">
            <span className="font-medium">{schema.properties?.[key]?.title ?? key}</span>: {t("fields.unsupported")}
          </p>
        ))}
        {ldapTlsUnchecked && (
          <p
            data-testid="identity-ldap-tls-warning"
            role="alert"
            className="rounded border border-danger/30 bg-danger/15 px-3 py-2 text-sm text-ink"
          >
            {t(locked ? "admin.identity.ldap.tlsWarningEnv" : "admin.identity.ldap.tlsWarning")}
          </p>
        )}
        {provider.kind === "saml" && (
          <p
            data-testid="identity-sha1-warning"
            className={cn(
              "rounded border px-3 py-2 text-xs",
              config.allow_sha1_signatures ? "border-danger/30 bg-danger/15 text-ink" : "border-hairline bg-surface-subtle text-muted",
            )}
          >
            {t("admin.identity.saml.sha1Warning")}
          </p>
        )}
        {certificate && (
          <div>
            <CopyButton text={certificate} testId="identity-copy-certificate" label={t("admin.identity.saml.copyCertificate")} />
          </div>
        )}
        {provider.kind === "saml" && (
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                data-testid="identity-refresh-metadata"
                disabled={refreshM.isPending || locked}
                onClick={() => refreshM.mutate()}
              >
                {t("admin.identity.saml.refresh")}
              </Button>
              {latest.metadata_fetched_at && (
                <span className="text-xs text-muted">
                  {t("admin.identity.saml.fetchedAt", { when: new Date(latest.metadata_fetched_at).toLocaleString() })}
                </span>
              )}
            </div>
            {metaError && (
              <p data-testid="identity-metadata-error" role="alert" className="text-sm text-danger">
                {metaError}
              </p>
            )}
          </div>
        )}
      </section>

      {!locked && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="primary"
            data-testid="identity-save"
            disabled={!dirty || saveM.isPending}
            onClick={() => {
              setSaved(false);
              saveM.mutate();
            }}
          >
            {t("common.save")}
          </Button>
          {saveM.isError && (
            <span data-testid="identity-save-error" role="alert" className="text-sm text-danger">
              {apiErrorMessage(saveM.error, t("admin.identity.saveError"))}
            </span>
          )}
          {saved && !dirty && (
            <span data-testid="identity-saved" className="text-sm text-muted">
              {t("common.saved")}
            </span>
          )}
        </div>
      )}

      <EndpointsBox provider={latest} />
    </div>
  );
}
