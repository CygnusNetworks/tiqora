import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { MultiSelectInput } from "@/components/fields/MultiSelectInput";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { formatDateTime } from "@/lib/format";
import { identityApi, providerFieldErrors, type IdentityProvider, type RoleRule } from "@/lib/identityApi";
import { toBcp47 } from "@/i18n";
import { GroupGrantsEditor } from "./GroupGrantsEditor";
import { useDirectory } from "./useDirectory";

type SyncMode = IdentityProvider["sync_mode"];
type Values = { role_rules: RoleRule[]; sync_mode: SyncMode };

const LDAP_GROUP_ATTRIBUTE = "ldap:group_dn";

function valuesOf(p: IdentityProvider): Values {
  return { role_rules: p.role_rules, sync_mode: p.sync_mode };
}

/** Roles and group rights that follow from values the identity provider sends. */
export function RolesTab({
  provider,
  onSaved,
  sampleAttributes,
}: {
  provider: IdentityProvider;
  onSaved: (p: IdentityProvider) => void;
  /** Attribute names of the last test sign-in, offered as suggestions. */
  sampleAttributes?: Record<string, string[]> | null;
}) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const locked = provider.read_only;
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

  const setRule = (index: number, patch: Partial<RoleRule>) =>
    setValues((v) => ({ ...v, role_rules: v.role_rules.map((r, i) => (i === index ? { ...r, ...patch } : r)) }));

  const saveM = useMutation({
    mutationFn: () => identityApi.update(provider.id, { role_rules: values.role_rules, sync_mode: values.sync_mode }),
    onSuccess: (p) => {
      const fresh = valuesOf(p);
      setBaseline(fresh);
      setValues(fresh);
      setSaved(true);
      onSaved(p);
      qc.invalidateQueries({ queryKey: ["admin", "identity", provider.id, "managed"] });
    },
  });
  const rulesError = providerFieldErrors(saveM.error).common.role_rules;

  const managedQ = useQuery({
    queryKey: ["admin", "identity", provider.id, "managed"],
    queryFn: ({ signal }) => identityApi.managedAssignments(provider.id, signal),
  });
  const locale = toBcp47(i18n.language);

  const suggestions: { value: string; label: string }[] =
    provider.kind === "ldap"
      ? [{ value: LDAP_GROUP_ATTRIBUTE, label: t("admin.identity.roles.ldapGroupDn") }]
      : Object.keys(sampleAttributes ?? {}).map((name) => ({ value: name, label: name }));

  return (
    <div className="space-y-4" data-testid="identity-roles">
      {locked && (
        <p data-testid="identity-env-badge" className="rounded border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink">
          {t("admin.identity.envBadge")}
        </p>
      )}

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.roles.rulesHeading")}</h2>
        <p className="text-xs text-muted">{t("admin.identity.roles.rulesHelp")}</p>
        {values.role_rules.some((r) => r.attribute.startsWith("scim:")) && (
          <p data-testid="identity-scim-hint" className="text-xs text-muted">
            {t("admin.identity.roles.scimHint")}
          </p>
        )}
        {values.role_rules.length === 0 && (
          <p data-testid="identity-rules-empty" className="text-sm text-muted">
            {t("admin.identity.roles.rulesEmpty")}
          </p>
        )}
        {values.role_rules.map((rule, i) => (
          <div key={i} data-testid={`identity-rule-${i}`} className="space-y-3 rounded border border-hairline p-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor={`identity-rule-${i}-attribute`} className="mb-1 block text-xs font-medium text-muted">
                  {t("admin.identity.roles.attribute")}
                </label>
                <input
                  id={`identity-rule-${i}-attribute`}
                  data-testid={`identity-rule-${i}-attribute`}
                  type="text"
                  disabled={locked}
                  value={rule.attribute}
                  onChange={(e) => setRule(i, { attribute: e.target.value })}
                  className="w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
                />
                {!locked && suggestions.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {suggestions.map((s) => (
                      <button
                        key={s.value}
                        type="button"
                        data-testid={`identity-rule-${i}-suggest-${s.value}`}
                        onClick={() => setRule(i, { attribute: s.value })}
                        className="rounded-full border border-dashed border-hairline px-2 py-0.5 text-[12px] text-muted hover:text-ink"
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <label htmlFor={`identity-rule-${i}-value`} className="mb-1 block text-xs font-medium text-muted">
                  {t("admin.identity.roles.value")}
                </label>
                <input
                  id={`identity-rule-${i}-value`}
                  data-testid={`identity-rule-${i}-value`}
                  type="text"
                  disabled={locked}
                  value={rule.value}
                  onChange={(e) => setRule(i, { value: e.target.value })}
                  className="w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </div>
            </div>
            <div>
              <div id={`identity-rule-${i}-roles-label`} className="mb-1 text-xs font-medium text-muted">
                {t("admin.identity.roles.roles")}
              </div>
              <MultiSelectInput
                testId={`identity-rule-${i}-roles`}
                labelId={`identity-rule-${i}-roles-label`}
                disabled={locked}
                options={roleOptions}
                value={(rule.role_ids ?? []).map(String)}
                onChange={(next) => setRule(i, { role_ids: next.map(Number) })}
              />
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-muted">{t("admin.identity.roles.groups")}</div>
              <GroupGrantsEditor
                testId={`identity-rule-${i}-groups`}
                disabled={locked}
                groups={groupOptions}
                grants={rule.groups ?? []}
                onChange={(next) => setRule(i, { groups: next })}
              />
            </div>
            {!locked && (
              <button
                type="button"
                data-testid={`identity-rule-${i}-remove`}
                onClick={() => setValues((v) => ({ ...v, role_rules: v.role_rules.filter((_, idx) => idx !== i) }))}
                className="text-xs text-muted hover:text-danger"
              >
                {t("admin.identity.roles.removeRule")}
              </button>
            )}
          </div>
        ))}
        {rulesError && (
          <p data-testid="identity-rules-error" role="alert" className="text-sm text-danger">
            {rulesError}
          </p>
        )}
        {!locked && (
          <Button
            type="button"
            data-testid="identity-rule-add"
            onClick={() =>
              setValues((v) => ({ ...v, role_rules: [...v.role_rules, { attribute: "", value: "", role_ids: [], groups: [] }] }))
            }
          >
            {t("admin.identity.roles.addRule")}
          </Button>
        )}
      </section>

      <section className="space-y-2 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.roles.syncHeading")}</h2>
        {(["managed", "authoritative"] as const).map((mode) => (
          <label key={mode} className="flex items-start gap-2">
            <input
              type="radio"
              name="identity-sync-mode"
              data-testid={`identity-sync-${mode}`}
              className="mt-1"
              disabled={locked}
              checked={values.sync_mode === mode}
              onChange={() => setValues((v) => ({ ...v, sync_mode: mode }))}
            />
            <span>
              <span className="block text-sm font-medium text-ink">{t(`admin.identity.roles.sync.${mode}`)}</span>
              <span className="block text-xs text-muted">{t(`admin.identity.roles.sync.${mode}Help`)}</span>
            </span>
          </label>
        ))}
      </section>

      {!locked && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="primary"
            data-testid="identity-roles-save"
            disabled={!dirty || saveM.isPending}
            onClick={() => {
              setSaved(false);
              saveM.mutate();
            }}
          >
            {t("common.save")}
          </Button>
          {saveM.isError && !rulesError && (
            <span data-testid="identity-roles-save-error" role="alert" className="text-sm text-danger">
              {apiErrorMessage(saveM.error, t("admin.identity.saveError"))}
            </span>
          )}
          {saved && !dirty && (
            <span data-testid="identity-roles-saved" className="text-sm text-muted">
              {t("common.saved")}
            </span>
          )}
        </div>
      )}

      <section className="space-y-2 rounded-lg border border-hairline bg-surface p-4" data-testid="identity-managed">
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.roles.managedHeading")}</h2>
        <p className="text-xs text-muted">{t("admin.identity.roles.managedHelp")}</p>
        {managedQ.isLoading && <Spinner />}
        {managedQ.isError && (
          <p role="alert" className="text-sm text-danger">
            {apiErrorMessage(managedQ.error, t("admin.identity.roles.managedError"))}
          </p>
        )}
        {managedQ.data && managedQ.data.length === 0 && (
          <p data-testid="identity-managed-empty" className="text-sm text-muted">
            {t("admin.identity.roles.managedEmpty")}
          </p>
        )}
        {managedQ.data && managedQ.data.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 pr-3 font-medium">{t("admin.identity.roles.managedAccount")}</th>
                <th className="py-1 pr-3 font-medium">{t("admin.identity.roles.managedAssignment")}</th>
                <th className="py-1 font-medium">{t("admin.identity.roles.managedSince")}</th>
              </tr>
            </thead>
            <tbody>
              {managedQ.data.map((m) => (
                <tr key={`${m.user_id}-${m.kind}-${m.target_id}-${m.permission_key}`} className="border-t border-hairline" data-testid="identity-managed-row">
                  <td className="py-1 pr-3 text-ink">{m.login}</td>
                  <td className="py-1 pr-3 text-ink">
                    {m.kind === "role"
                      ? t("admin.identity.roles.managedRole", { name: m.target_name })
                      : t("admin.identity.roles.managedGroup", { name: m.target_name, permission: m.permission_key })}
                  </td>
                  <td className="py-1 text-muted">{formatDateTime(m.created, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
