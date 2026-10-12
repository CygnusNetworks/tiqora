import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { TagInput } from "@/components/ui/TagInput";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  identityApi,
  providerFieldErrors,
  type IdentityProvider,
  type MfaPolicy,
  type ProviderChanges,
} from "@/lib/identityApi";

const IDLE_MIN_SECONDS = 300;
const ABSOLUTE_MIN_SECONDS = 900;

type Fallback = MfaPolicy["fallback"];
const FALLBACKS: Fallback[] = ["local_second_factor", "reject", "local_second_factor_or_enroll"];

type PolicyKey = keyof MfaPolicy;
type PolicyForm = Required<MfaPolicy>;

const sameValue = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function Field({
  label,
  help,
  error,
  htmlFor,
  children,
}: {
  label: string;
  help?: string;
  error?: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-muted">
        {label}
      </label>
      {children}
      {help && <p className="mt-1 text-xs text-muted">{help}</p>}
      {error && (
        <p role="alert" className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

const inputClass =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-60";

/** Second factor required from the identity provider, and the lifetime of its sessions. */
export function SessionTab({
  provider,
  onSaved,
}: {
  provider: IdentityProvider;
  onSaved: (p: IdentityProvider) => void;
}) {
  const { t } = useTranslation();
  const capsQ = useQuery({
    queryKey: ["admin", "identity", "capabilities"],
    queryFn: ({ signal }) => identityApi.capabilities(signal),
  });

  const stored: PolicyForm = {
    oidc_amr_values: [],
    oidc_acr_values: [],
    saml_authn_context_classes: [],
    saml_mfa_attribute_values: [],
    ...provider.mfa_policy,
  };
  const [requireMfa, setRequireMfa] = useState(provider.require_mfa_claim);
  const [policy, setPolicy] = useState<PolicyForm>(stored);
  const [idleSeconds, setIdleSeconds] = useState<number | null>(provider.session_idle_seconds ?? null);
  const [absSeconds, setAbsSeconds] = useState<number | null>(provider.session_absolute_seconds ?? null);

  const saveM = useMutation({
    mutationFn: (changes: ProviderChanges) => identityApi.update(provider.id, changes),
    onSuccess: (p) => onSaved(p),
  });

  if (provider.source === "env") {
    return (
      <p data-testid="session-env-readonly" className="rounded border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink">
        {t("admin.identity.session.envReadOnly")}
      </p>
    );
  }
  if (capsQ.isLoading) return <Spinner />;
  const caps = capsQ.data;
  if (!caps) {
    return (
      <p role="alert" className="text-sm text-danger">
        {apiErrorMessage(capsQ.error, t("admin.identity.session.loadError"))}
      </p>
    );
  }

  const idleMax = caps.session_idle_max_seconds;
  const absMax = caps.session_absolute_max_seconds;
  const useGlobal = idleSeconds === null && absSeconds === null;
  const isOidc = provider.kind === "oidc";
  const errors = saveM.isError ? providerFieldErrors(saveM.error).common : {};
  const errorFor = (key: string) => errors[key] ?? errors[`mfa_policy.${key}`];

  const set = <K extends PolicyKey>(key: K, value: PolicyForm[K]) => setPolicy((cur) => ({ ...cur, [key]: value }));

  const idleError =
    idleSeconds !== null && (idleSeconds < IDLE_MIN_SECONDS || idleSeconds > idleMax)
      ? t("admin.identity.session.idleRange", { min: IDLE_MIN_SECONDS / 60, max: Math.floor(idleMax / 60) })
      : undefined;
  const absError =
    absSeconds !== null && (absSeconds < ABSOLUTE_MIN_SECONDS || absSeconds > absMax)
      ? t("admin.identity.session.absoluteRange", { min: ABSOLUTE_MIN_SECONDS / 3600, max: absMax / 3600 })
      : undefined;

  // Only what differs from the stored provider goes into the PATCH.
  const changes: ProviderChanges = {};
  if (requireMfa !== provider.require_mfa_claim) changes.require_mfa_claim = requireMfa;
  const policyChanges: Record<string, unknown> = {};
  for (const key of Object.keys(policy) as PolicyKey[]) {
    if (!sameValue(policy[key], stored[key])) policyChanges[key] = policy[key];
  }
  if (Object.keys(policyChanges).length > 0) changes.mfa_policy = policyChanges as ProviderChanges["mfa_policy"];
  if (idleSeconds !== (provider.session_idle_seconds ?? null)) changes.session_idle_seconds = idleSeconds;
  if (absSeconds !== (provider.session_absolute_seconds ?? null)) changes.session_absolute_seconds = absSeconds;
  const dirty = Object.keys(changes).length > 0;
  const invalid = !!idleError || !!absError;

  const onUseGlobal = (checked: boolean) => {
    if (checked) {
      setIdleSeconds(null);
      setAbsSeconds(null);
    } else {
      setIdleSeconds(idleMax);
      setAbsSeconds(absMax);
    }
  };

  return (
    <form
      className="space-y-4"
      data-testid="identity-session"
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty && !invalid) saveM.mutate(changes);
      }}
    >
      <section className="space-y-4 rounded-lg border border-hairline bg-surface p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div id="session-mfa-label" className="text-sm font-medium text-ink">
              {t("admin.identity.session.requireMfa")}
            </div>
            <p className="text-xs text-muted">{t("admin.identity.session.requireMfaHelp")}</p>
          </div>
          <Switch testId="session-require-mfa" aria-labelledby="session-mfa-label" checked={requireMfa} onChange={setRequireMfa} />
        </div>

        {errors["mfa_policy"] && (
          <p role="alert" data-testid="session-mfa-policy-error" className="text-xs text-danger">
            {errors["mfa_policy"]}
          </p>
        )}
        {requireMfa && (
          <div className="space-y-4" data-testid="session-mfa-policy">
            <fieldset>
              <legend className="mb-1 text-xs font-medium text-muted">{t("admin.identity.session.fallback.title")}</legend>
              <div className="space-y-1">
                {FALLBACKS.map((f) => (
                  <label key={f} className="flex items-start gap-2 text-sm text-ink">
                    <input
                      type="radio"
                      name="session-fallback"
                      data-testid={`session-fallback-${f}`}
                      checked={policy.fallback === f}
                      onChange={() => set("fallback", f)}
                      className="mt-1"
                    />
                    <span>
                      {t(`admin.identity.session.fallback.${f}`)}
                      {f === "local_second_factor_or_enroll" && (
                        <span className="block text-xs text-muted">
                          {t("admin.identity.session.fallback.enrollHelp")}
                        </span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
              {errorFor("fallback") && (
                <p role="alert" className="mt-1 text-xs text-danger">
                  {errorFor("fallback")}
                </p>
              )}
            </fieldset>

            {isOidc ? (
              <div className="space-y-3" data-testid="session-oidc-fields">
                <Field
                  label={t("admin.identity.session.oidcAmr")}
                  help={t("admin.identity.session.oidcAmrHelp")}
                  error={errorFor("oidc_amr_values")}
                  htmlFor="session-oidc-amr-input"
                >
                  <TagInput id="session-oidc-amr-input" testId="session-oidc-amr" value={policy.oidc_amr_values} onChange={(v) => set("oidc_amr_values", v)} placeholder="mfa" />
                </Field>
                <Field
                  label={t("admin.identity.session.oidcAcr")}
                  help={t("admin.identity.session.oidcAcrHelp")}
                  error={errorFor("oidc_acr_values")}
                  htmlFor="session-oidc-acr-input"
                >
                  <TagInput id="session-oidc-acr-input" testId="session-oidc-acr" value={policy.oidc_acr_values} onChange={(v) => set("oidc_acr_values", v)} />
                </Field>
                <Field
                  label={t("admin.identity.session.oidcRequestAcr")}
                  help={t("admin.identity.session.oidcRequestAcrHelp")}
                  error={errorFor("oidc_request_acr_values")}
                  htmlFor="session-oidc-request-acr"
                >
                  <input id="session-oidc-request-acr" data-testid="session-oidc-request-acr" className={inputClass} maxLength={500} value={policy.oidc_request_acr_values} onChange={(e) => set("oidc_request_acr_values", e.target.value)} />
                </Field>
              </div>
            ) : (
              <div className="space-y-3" data-testid="session-saml-fields">
                <Field
                  label={t("admin.identity.session.samlClasses")}
                  help={t("admin.identity.session.samlClassesHelp")}
                  error={errorFor("saml_authn_context_classes")}
                  htmlFor="session-saml-classes-input"
                >
                  <TagInput id="session-saml-classes-input" testId="session-saml-classes" value={policy.saml_authn_context_classes} onChange={(v) => set("saml_authn_context_classes", v)} />
                </Field>
                <Field
                  label={t("admin.identity.session.samlAttribute")}
                  help={t("admin.identity.session.samlAttributeHelp")}
                  error={errorFor("saml_mfa_attribute")}
                  htmlFor="session-saml-attribute"
                >
                  <input id="session-saml-attribute" data-testid="session-saml-attribute" className={inputClass} maxLength={200} value={policy.saml_mfa_attribute} onChange={(e) => set("saml_mfa_attribute", e.target.value)} />
                </Field>
                <Field
                  label={t("admin.identity.session.samlAttributeValues")}
                  error={errorFor("saml_mfa_attribute_values")}
                  htmlFor="session-saml-values-input"
                >
                  <TagInput id="session-saml-values-input" testId="session-saml-values" value={policy.saml_mfa_attribute_values} onChange={(v) => set("saml_mfa_attribute_values", v)} />
                </Field>
                <label className="flex items-center gap-2 text-sm text-ink">
                  <input type="checkbox" data-testid="session-saml-request" checked={policy.saml_request_authn_context} onChange={(e) => set("saml_request_authn_context", e.target.checked)} />
                  {t("admin.identity.session.samlRequest")}
                </label>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.session.lifetime")}</h2>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" data-testid="session-use-global" checked={useGlobal} onChange={(e) => onUseGlobal(e.target.checked)} />
          {t("admin.identity.session.useGlobal")}
        </label>
        {!useGlobal && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={t("admin.identity.session.idle")} error={idleError ?? errorFor("session_idle_seconds")} htmlFor="session-idle">
              <input
                id="session-idle"
                type="number"
                data-testid="session-idle"
                className={inputClass}
                min={IDLE_MIN_SECONDS / 60}
                max={Math.floor(idleMax / 60)}
                step={1}
                value={idleSeconds === null ? "" : Math.round(idleSeconds / 60)}
                onChange={(e) => setIdleSeconds(e.target.value === "" ? null : Math.round(Number(e.target.value) * 60))}
              />
            </Field>
            <Field label={t("admin.identity.session.absolute")} error={absError ?? errorFor("session_absolute_seconds")} htmlFor="session-absolute">
              <input
                id="session-absolute"
                type="number"
                data-testid="session-absolute"
                className={inputClass}
                min={ABSOLUTE_MIN_SECONDS / 3600}
                max={absMax / 3600}
                step={0.25}
                value={absSeconds === null ? "" : absSeconds / 3600}
                onChange={(e) => setAbsSeconds(e.target.value === "" ? null : Math.round(Number(e.target.value) * 3600))}
              />
            </Field>
          </div>
        )}
        <p className="text-xs text-muted">
          {t("admin.identity.session.lifetimeHelp", { idle: Math.floor(idleMax / 60), hours: absMax / 3600 })}
        </p>
      </section>

      {saveM.isError && Object.keys(errors).length === 0 && (
        <p role="alert" data-testid="session-save-error" className="text-sm text-danger">
          {apiErrorMessage(saveM.error, t("admin.identity.saveError"))}
        </p>
      )}
      <Button type="submit" variant="primary" data-testid="session-save" disabled={!dirty || invalid || saveM.isPending}>
        {t("common.save")}
      </Button>
    </form>
  );
}
