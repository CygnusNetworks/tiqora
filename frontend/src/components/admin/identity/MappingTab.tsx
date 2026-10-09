import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { MultiSelectInput } from "@/components/fields/MultiSelectInput";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { TagInput } from "@/components/ui/TagInput";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  identityApi,
  providerFieldErrors,
  type AttributeMap,
  type IdentityProvider,
  type JitDefaults,
  type ProviderChanges,
} from "@/lib/identityApi";
import { GroupGrantsEditor } from "./GroupGrantsEditor";
import { LoginPlanView } from "./LoginPlanView";
import { useDirectory } from "./useDirectory";

type MatchBy = IdentityProvider["match_by"];
type Values = { attribute_map: AttributeMap; match_by: MatchBy; jit_enabled: boolean; jit_defaults: JitDefaults };

const FIELDS = ["login", "email", "first_name", "last_name", "customer_id", "mobile"] as const;
const PREVIEW_DEBOUNCE_MS = 400;

function valuesOf(p: IdentityProvider): Values {
  return {
    attribute_map: p.attribute_map,
    match_by: p.match_by,
    jit_enabled: p.jit_enabled,
    jit_defaults: p.jit_defaults,
  };
}

/** Which source value fills which Tiqora field, how an account is found, and automatic creation. */
export function MappingTab({
  provider,
  sampleAttributes,
  onSaved,
}: {
  provider: IdentityProvider;
  sampleAttributes: Record<string, string[]> | null;
  onSaved: (p: IdentityProvider) => void;
}) {
  const { t } = useTranslation();
  const locked = provider.read_only;
  const forAgents = provider.audience !== "customer";
  const forCustomers = provider.audience !== "agent";
  const { roleOptions, groupOptions } = useDirectory();

  const [baseline, setBaseline] = useState<Values>(() => valuesOf(provider));
  const [values, setValues] = useState<Values>(baseline);
  const [saved, setSaved] = useState(false);
  const dirty = JSON.stringify(values) !== JSON.stringify(baseline);

  const seen = useRef(provider);
  useEffect(() => {
    if (seen.current === provider) return;
    seen.current = provider;
    if (!dirty) {
      const fresh = valuesOf(provider);
      setBaseline(fresh);
      setValues(fresh);
    }
    // dirty is read at the moment the prop changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const setMap = (key: (typeof FIELDS)[number], next: string[]) =>
    setValues((v) => ({ ...v, attribute_map: { ...v.attribute_map, [key]: next } }));
  const setJit = (patch: Partial<JitDefaults>) =>
    setValues((v) => ({ ...v, jit_defaults: { ...v.jit_defaults, ...patch } }));

  const saveM = useMutation({
    mutationFn: () => identityApi.update(provider.id, { ...values } satisfies ProviderChanges),
    onSuccess: (p) => {
      const fresh = valuesOf(p);
      setBaseline(fresh);
      setValues(fresh);
      setSaved(true);
      onSaved(p);
    },
  });
  const errors = providerFieldErrors(saveM.error).common;

  const suggestions = useMemo(
    () => Object.keys(sampleAttributes ?? {}).map((name) => ({ name })),
    [sampleAttributes],
  );

  // Live preview of what a sign-in with the sample values would do, including unsaved edits.
  const requestKey = JSON.stringify({
    attributes: sampleAttributes,
    audience: provider.audience === "customer" ? "customer" : "agent",
    draft: dirty && !locked ? values : null,
  });
  const [debounced, setDebounced] = useState(requestKey);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(requestKey), PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [requestKey]);
  const previewQ = useQuery({
    queryKey: ["admin", "identity", provider.id, "preview", debounced],
    enabled: !!sampleAttributes,
    placeholderData: keepPreviousData,
    retry: false,
    queryFn: ({ signal }) => identityApi.preview(provider.id, JSON.parse(debounced), signal),
  });

  const domains = values.jit_defaults.allowed_email_domains ?? [];
  const config = provider.config as Record<string, unknown>;
  const emailUntrusted = values.match_by === "email" && provider.kind === "oidc" && config.email_must_be_verified === false;
  const entraEmail = values.match_by === "email" && provider.preset === "entra_id";

  return (
    <div className="space-y-4" data-testid="identity-mapping">
      {locked && (
        <p data-testid="identity-env-badge" className="rounded border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink">
          {t("admin.identity.envBadge")}
        </p>
      )}

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.mapping.fieldsHeading")}</h2>
        <p className="text-xs text-muted">{t("admin.identity.mapping.fieldsHelp")}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          {FIELDS.filter((f) => f !== "customer_id" || forCustomers).map((key) => (
            <div key={key}>
              <label htmlFor={`identity-map-${key}-input`} className="mb-1 block text-xs font-medium text-muted">
                {t(`admin.identity.mapping.field.${key}`)}
              </label>
              {locked ? (
                <p data-testid={`identity-map-${key}`} className="text-sm text-ink">
                  {(values.attribute_map[key] ?? []).join(", ") || "–"}
                </p>
              ) : (
                <TagInput
                  id={`identity-map-${key}-input`}
                  testId={`identity-map-${key}`}
                  value={values.attribute_map[key] ?? []}
                  onChange={(next) => setMap(key, next)}
                  suggestions={suggestions}
                  placeholder={t("admin.identity.mapping.sourcePlaceholder")}
                />
              )}
            </div>
          ))}
        </div>
        {errors.attribute_map && (
          <p role="alert" className="text-sm text-danger">
            {errors.attribute_map}
          </p>
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <div>
          <label htmlFor="identity-match-by" className="mb-1 block text-xs font-medium text-muted">
            {t("admin.identity.mapping.matchBy")}
          </label>
          <SelectField
            id="identity-match-by"
            testId="identity-match-by"
            disabled={locked}
            value={values.match_by}
            onChange={(v) => setValues((cur) => ({ ...cur, match_by: v as MatchBy }))}
            items={(["login", "email"] as const).map((m) => ({ value: m, label: t(`admin.identity.mapping.matchByOption.${m}`) }))}
          />
          <p className="mt-1 text-xs text-muted">{t("admin.identity.mapping.matchByHelp")}</p>
        </div>
        {emailUntrusted && (
          <p data-testid="identity-email-untrusted-warning" role="alert" className="rounded border border-danger/30 bg-danger/15 px-3 py-2 text-sm text-ink">
            {t("admin.identity.mapping.emailUntrustedWarning")}
          </p>
        )}
        {entraEmail && (
          <p data-testid="identity-entra-email-warning" className="rounded border border-escalation/30 bg-escalation/15 px-3 py-2 text-sm text-ink">
            {t("admin.identity.mapping.entraEmailWarning")}
          </p>
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <div className="flex items-center gap-3">
          <Switch
            id="identity-jit"
            testId="identity-jit"
            disabled={locked}
            checked={values.jit_enabled}
            onChange={(on) => setValues((v) => ({ ...v, jit_enabled: on }))}
          />
          <label htmlFor="identity-jit" className="text-sm font-medium text-ink">
            {t("admin.identity.mapping.jit")}
          </label>
        </div>
        <p className="text-xs text-muted">{t("admin.identity.mapping.jitHelp")}</p>
        {values.jit_enabled && (
          <div className="space-y-3" data-testid="identity-jit-details">
            <div>
              <div id="identity-jit-domains-label" className="mb-1 text-xs font-medium text-muted">
                {t("admin.identity.mapping.allowedDomains")}
              </div>
              {locked ? (
                <p className="text-sm text-ink">{domains.join(", ") || "–"}</p>
              ) : (
                <TagInput
                  testId="identity-jit-domains"
                  value={domains}
                  onChange={(next) => setJit({ allowed_email_domains: next })}
                  placeholder="example.org"
                />
              )}
              <p className="mt-1 text-xs text-muted">{t("admin.identity.mapping.allowedDomainsHelp")}</p>
              {domains.length === 0 && (
                <p data-testid="identity-jit-domain-warning" role="alert" className="mt-2 rounded border border-escalation/30 bg-escalation/15 px-3 py-2 text-sm text-ink">
                  {t("admin.identity.mapping.noDomainWarning")}
                </p>
              )}
              {errors.jit_defaults && (
                <p role="alert" className="mt-1 text-sm text-danger">
                  {errors.jit_defaults}
                </p>
              )}
            </div>
            {forAgents && (
              <div className="space-y-2">
                <div id="identity-jit-roles-label" className="text-xs font-medium text-muted">
                  {t("admin.identity.mapping.initialRoles")}
                </div>
                <MultiSelectInput
                  testId="identity-jit-roles"
                  labelId="identity-jit-roles-label"
                  disabled={locked}
                  options={roleOptions}
                  value={(values.jit_defaults.initial_role_ids ?? []).map(String)}
                  onChange={(next) => setJit({ initial_role_ids: next.map(Number) })}
                />
                <div className="text-xs font-medium text-muted">{t("admin.identity.mapping.initialGroups")}</div>
                <GroupGrantsEditor
                  testId="identity-jit-groups"
                  disabled={locked}
                  groups={groupOptions}
                  grants={values.jit_defaults.initial_groups ?? []}
                  onChange={(next) => setJit({ initial_groups: next })}
                />
              </div>
            )}
            {forCustomers && (
              <div>
                <label htmlFor="identity-jit-customer-id" className="mb-1 block text-xs font-medium text-muted">
                  {t("admin.identity.mapping.fixedCustomerId")}
                </label>
                <input
                  id="identity-jit-customer-id"
                  data-testid="identity-jit-customer-id"
                  type="text"
                  disabled={locked}
                  value={values.jit_defaults.customer_id ?? ""}
                  onChange={(e) => setJit({ customer_id: e.target.value })}
                  className="w-full max-w-sm rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
                />
                <p className="mt-1 text-xs text-muted">{t("admin.identity.mapping.fixedCustomerIdHelp")}</p>
              </div>
            )}
          </div>
        )}
        <div className="flex items-center gap-3">
          <Switch
            id="identity-update-profile"
            testId="identity-update-profile"
            disabled={locked}
            checked={values.jit_defaults.update_profile_on_login ?? true}
            onChange={(on) => setJit({ update_profile_on_login: on })}
          />
          <label htmlFor="identity-update-profile" className="text-sm font-medium text-ink">
            {t("admin.identity.mapping.updateProfile")}
          </label>
        </div>
      </section>

      {!locked && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="primary"
            data-testid="identity-mapping-save"
            disabled={!dirty || saveM.isPending}
            onClick={() => {
              setSaved(false);
              saveM.mutate();
            }}
          >
            {t("common.save")}
          </Button>
          {saveM.isError && (
            <span data-testid="identity-mapping-save-error" role="alert" className="text-sm text-danger">
              {apiErrorMessage(saveM.error, t("admin.identity.saveError"))}
            </span>
          )}
          {saved && !dirty && (
            <span data-testid="identity-mapping-saved" className="text-sm text-muted">
              {t("common.saved")}
            </span>
          )}
        </div>
      )}

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="identity-preview">
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.mapping.previewHeading")}</h2>
        {!sampleAttributes ? (
          <p data-testid="identity-preview-empty" className="text-sm text-muted">
            {t("admin.identity.mapping.previewEmpty")}
          </p>
        ) : (
          <>
            <p className="text-xs text-muted">{t("admin.identity.mapping.previewHelp")}</p>
            {previewQ.isError && (
              <p data-testid="identity-preview-error" role="alert" className="text-sm text-danger">
                {apiErrorMessage(previewQ.error, t("admin.identity.mapping.previewError"))}
              </p>
            )}
            {previewQ.isLoading && <Spinner />}
            {previewQ.data && <LoginPlanView plan={previewQ.data} />}
          </>
        )}
      </section>
    </div>
  );
}
