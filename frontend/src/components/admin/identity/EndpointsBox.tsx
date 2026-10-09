import { useTranslation } from "react-i18next";
import type { IdentityProvider, ProviderEndpoints } from "@/lib/identityApi";
import { CopyButton } from "./CopyButton";

type Key = keyof Omit<ProviderEndpoints, "base_url_missing">;

/** Which addresses the identity provider's admin console needs, per kind and audience. */
function endpointKeys(provider: IdentityProvider): Key[] {
  const agent = provider.audience !== "customer";
  const customer = provider.audience !== "agent";
  if (provider.kind === "oidc") {
    return [
      ...(agent ? (["agent_redirect_uri"] as const) : []),
      ...(customer ? (["customer_redirect_uri"] as const) : []),
      "backchannel_logout_url",
    ];
  }
  if (provider.kind === "saml") {
    return [
      "sp_entity_id",
      ...(agent ? (["agent_acs_url"] as const) : []),
      ...(customer ? (["customer_acs_url"] as const) : []),
      "sp_metadata_url",
      "slo_url",
    ];
  }
  return [];
}

/** Addresses to enter at the identity provider (redirect, ACS, metadata, logout), each copyable. */
export function EndpointsBox({ provider }: { provider: IdentityProvider }) {
  const { t } = useTranslation();
  const keys = endpointKeys(provider);
  if (keys.length === 0) return null;
  const { endpoints } = provider;
  return (
    <section className="space-y-2 rounded-lg border border-hairline bg-surface p-4" data-testid="identity-endpoints">
      <div>
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.endpoints.title")}</h2>
        <p className="text-xs text-muted">{t("admin.identity.endpoints.hint")}</p>
      </div>
      {endpoints.base_url_missing && (
        <p
          data-testid="identity-endpoints-base-missing"
          className="rounded border border-escalation/30 bg-escalation/15 px-3 py-2 text-sm text-ink"
        >
          {t("admin.identity.endpoints.baseUrlMissing")}
        </p>
      )}
      <ul className="space-y-2">
        {keys.map((key) => {
          const value = endpoints[key];
          if (!value) return null;
          return (
            <li key={key} data-testid={`identity-endpoint-${key}`} className="flex flex-wrap items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium text-muted">{t(`admin.identity.endpoints.labels.${key}`)}</div>
                <code className="block break-all text-[12.5px] text-ink">{value}</code>
              </div>
              <CopyButton text={value} testId={`identity-copy-${key}`} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}
