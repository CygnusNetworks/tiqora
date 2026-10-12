import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ConnectionTab } from "@/components/admin/identity/ConnectionTab";
import { LinksTab } from "@/components/admin/identity/LinksTab";
import { MappingTab } from "@/components/admin/identity/MappingTab";
import { ProvisioningTab } from "@/components/admin/identity/ProvisioningTab";
import { RolesTab } from "@/components/admin/identity/RolesTab";
import { SessionTab } from "@/components/admin/identity/SessionTab";
import { TestTab } from "@/components/admin/identity/TestTab";
import { ProviderIcon } from "@/components/auth/ProviderIcon";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { apiErrorMessage } from "@/lib/avatarApi";
import { identityApi, type IdentityProvider, type ProviderChanges } from "@/lib/identityApi";
import { IDENTITY_LIST_KEY } from "./IdentityProvidersPage";

export type IdentityProviderTab = "connection" | "mapping" | "roles" | "provisioning" | "session" | "test" | "links";
export type IdentityProviderSearch = { tab?: IdentityProviderTab; test?: string };

const TABS: IdentityProviderTab[] = ["connection", "mapping", "roles", "provisioning", "session", "test", "links"];
const ROUTE = "/admin/identity-providers/$providerId" as const;

export function IdentityProviderPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { providerId } = useParams({ from: ROUTE });
  const search = useSearch({ from: ROUTE });
  const id = Number(providerId);
  const [draft, setDraft] = useState<ProviderChanges | undefined>(undefined);
  // Values of the last test sign-in; they drive the live preview on the mapping tab.
  const [sample, setSample] = useState<Record<string, string[]> | null>(null);
  // Mapping and roles stay mounted once visited so unsaved edits survive tab switches.
  const [visited, setVisited] = useState<ReadonlySet<IdentityProviderTab>>(new Set());

  const providerKey = ["admin", "identity", id] as const;
  const providerQ = useQuery({ queryKey: providerKey, queryFn: ({ signal }) => identityApi.get(id, signal) });
  const kindsQ = useQuery({
    queryKey: ["admin", "identity", "kinds"],
    queryFn: ({ signal }) => identityApi.kinds(signal),
    staleTime: 5 * 60 * 1000,
  });
  const capsQ = useQuery({
    queryKey: ["admin", "identity", "capabilities"],
    queryFn: ({ signal }) => identityApi.capabilities(signal),
  });

  const tab: IdentityProviderTab = search.test ? "test" : (search.tab ?? "connection");
  const provider = providerQ.data;
  useEffect(() => {
    setVisited((cur) => (cur.has(tab) ? cur : new Set(cur).add(tab)));
  }, [tab]);

  const onSaved = (p: IdentityProvider) => {
    qc.setQueryData(providerKey, p);
    qc.invalidateQueries({ queryKey: IDENTITY_LIST_KEY });
  };

  if (providerQ.isLoading || kindsQ.isLoading || capsQ.isLoading) {
    return (
      <div className="p-4">
        <Spinner />
      </div>
    );
  }
  const failure = providerQ.error ?? kindsQ.error ?? capsQ.error;
  if (!provider || !kindsQ.data || !capsQ.data) {
    return (
      <div className="p-4">
        <p role="alert" className="text-sm text-danger">
          {apiErrorMessage(failure, t("admin.identity.loadError"))}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4" data-testid="admin-identity-provider-page">
      <div>
        <Link to="/admin/identity-providers" className="text-xs text-muted hover:text-ink">
          ← {t("admin.identity.title")}
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <ProviderIcon icon={provider.button_icon} className="h-5 w-5 shrink-0" />
          <h1 className="font-display text-xl font-semibold text-ink">{provider.name}</h1>
          <Badge tone="muted">{t(`admin.identity.kind.${provider.kind}`)}</Badge>
          <Badge tone={provider.enabled ? "success" : "muted"}>
            {provider.enabled ? t("admin.identity.state.enabled") : t("admin.identity.state.disabled")}
          </Badge>
        </div>
      </div>

      <Tabs
        items={TABS.filter((id) => (id !== "provisioning" && id !== "session") || provider.kind !== "ldap").map((id) => ({ id, label: t(`admin.identity.tabs.${id}`) }))}
        value={tab}
        onChange={(next) =>
          navigate({
            to: ROUTE,
            params: { providerId: String(provider.id) },
            search: { tab: next as IdentityProviderTab },
          })
        }
      />

      {/* Kept mounted so unsaved edits survive a visit to the test tab. */}
      <div hidden={tab !== "connection"}>
        <ConnectionTab
          key={provider.id}
          provider={provider}
          kinds={kindsQ.data}
          capabilities={capsQ.data}
          onSaved={onSaved}
          onDraftChange={setDraft}
        />
      </div>
      {(tab === "mapping" || visited.has("mapping")) && (
        <div hidden={tab !== "mapping"}>
          <MappingTab key={provider.id} provider={provider} sampleAttributes={sample} onSaved={onSaved} />
        </div>
      )}
      {(tab === "roles" || visited.has("roles")) && (
        <div hidden={tab !== "roles"}>
          <RolesTab key={provider.id} provider={provider} sampleAttributes={sample} onSaved={onSaved} />
        </div>
      )}
      {tab === "provisioning" && provider.kind !== "ldap" && (
        <ProvisioningTab key={provider.id} provider={provider} onSaved={onSaved} />
      )}
      {tab === "session" && provider.kind !== "ldap" && (
        <SessionTab key={provider.id} provider={provider} onSaved={onSaved} />
      )}
      {tab === "test" && (
        <TestTab
          provider={provider}
          testId={search.test}
          draft={draft}
          onUseAttributes={(attributes) => {
            setSample(attributes);
            navigate({ to: ROUTE, params: { providerId: String(provider.id) }, search: { tab: "mapping" } });
          }}
        />
      )}
      {tab === "links" && <LinksTab provider={provider} />}
    </div>
  );
}
