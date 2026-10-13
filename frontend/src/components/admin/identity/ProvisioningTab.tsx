import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { apiErrorMessage } from "@/lib/avatarApi";
import { formatDateTime } from "@/lib/format";
import { identityApi, type IdentityProvider, type ProviderChanges, type ScimToken } from "@/lib/identityApi";
import { toBcp47 } from "@/i18n";
import { CopyButton } from "./CopyButton";
import { ScimEventsList } from "./ScimEventsList";
import { ScimTokenDialog } from "./ScimTokenDialog";

const PAGE = 25;

function Pager({
  offset,
  total,
  onChange,
  testId,
}: {
  offset: number;
  total: number;
  onChange: (offset: number) => void;
  testId: string;
}) {
  const { t } = useTranslation();
  if (total <= PAGE) return null;
  const to = Math.min(offset + PAGE, total);
  return (
    <div className="flex items-center justify-end gap-2 text-xs text-muted" data-testid={testId}>
      <span>{t("admin.identity.scim.range", { from: offset + 1, to, total })}</span>
      <Button type="button" size="sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE))}>
        {t("admin.identity.scim.prev")}
      </Button>
      <Button type="button" size="sm" disabled={to >= total} onClick={() => onChange(offset + PAGE)}>
        {t("admin.identity.scim.next")}
      </Button>
    </div>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

/** SCIM provisioning: switches, address, keys, provisioned accounts and groups, request log. */
export function ProvisioningTab({
  provider,
  onSaved,
}: {
  provider: IdentityProvider;
  onSaved: (p: IdentityProvider) => void;
}) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const { confirm, dialog } = useConfirm();
  const locale = toBcp47(i18n.language);
  const id = provider.id;
  const key = ["identity", id, "scim"] as const;

  const [dialogOpen, setDialogOpen] = useState(false);
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 250);
  const [accountOffset, setAccountOffset] = useState(0);
  const [showDeleted, setShowDeleted] = useState(false);
  const [groupOffset, setGroupOffset] = useState(0);

  const statusQ = useQuery({ queryKey: [...key, "status"], queryFn: ({ signal }) => identityApi.getScimStatus(id, signal) });
  const accountsQ = useQuery({
    queryKey: [...key, "accounts", q, accountOffset, showDeleted],
    queryFn: ({ signal }) =>
      identityApi.listScimAccounts(
        id,
        { offset: accountOffset, limit: PAGE, q: q || undefined, include_deleted: showDeleted || undefined },
        signal,
      ),
    enabled: statusQ.data?.available === true,
  });
  const groupsQ = useQuery({
    queryKey: [...key, "groups", groupOffset],
    queryFn: ({ signal }) => identityApi.listScimGroups(id, { offset: groupOffset, limit: PAGE }, signal),
    enabled: statusQ.data?.available === true,
  });

  const saveM = useMutation({
    mutationFn: (changes: ProviderChanges) => identityApi.update(id, changes),
    onSuccess: (p) => {
      onSaved(p);
      qc.invalidateQueries({ queryKey: [...key, "status"] });
    },
  });
  const revokeM = useMutation({
    mutationFn: (tokenId: number) => identityApi.revokeScimToken(id, tokenId),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });

  const onRevoke = async (token: ScimToken) => {
    const ok = await confirm({
      title: t("admin.identity.scim.tokens.revokeTitle"),
      message: t("admin.identity.scim.tokens.revokeConfirm", { name: token.name }),
      confirmLabel: t("admin.identity.scim.tokens.revoke"),
      variant: "danger",
    });
    if (ok) revokeM.mutate(token.id);
  };

  if (statusQ.isLoading) return <Spinner />;
  const status = statusQ.data;
  if (!status) {
    return (
      <p role="alert" className="text-sm text-danger">
        {apiErrorMessage(statusQ.error, t("admin.identity.scim.loadError"))}
      </p>
    );
  }

  if (!status.available) {
    return (
      <div className="space-y-3" data-testid="scim-unavailable">
        <p className="rounded border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink">
          {provider.read_only ? t("admin.identity.scim.envReadOnly") : t("admin.identity.scim.unavailable")}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="identity-provisioning">
      <section className="space-y-4 rounded-lg border border-hairline bg-surface p-4">
        <div>
          <h2 className="text-sm font-semibold text-ink">{t("admin.identity.scim.title")}</h2>
          <p className="text-xs text-muted">{t("admin.identity.scim.intro")}</p>
        </div>

        <div className="flex items-start justify-between gap-4">
          <div>
            <div id="scim-enabled-label" className="text-sm font-medium text-ink">
              {t("admin.identity.scim.enabled")}
            </div>
            <p className="text-xs text-muted">{t("admin.identity.scim.enabledHelp")}</p>
          </div>
          <Switch
            testId="scim-enabled"
            aria-labelledby="scim-enabled-label"
            checked={provider.scim_enabled}
            disabled={saveM.isPending}
            onChange={(v) => saveM.mutate({ scim_enabled: v })}
          />
        </div>
        <div className="flex items-start justify-between gap-4">
          <div>
            <div id="scim-adopt-label" className="text-sm font-medium text-ink">
              {t("admin.identity.scim.adopt")}
            </div>
            <p className="text-xs text-muted">{t("admin.identity.scim.adoptHelp")}</p>
          </div>
          <Switch
            testId="scim-adopt"
            aria-labelledby="scim-adopt-label"
            checked={provider.scim_adopt_existing}
            disabled={saveM.isPending}
            onChange={(v) => saveM.mutate({ scim_adopt_existing: v })}
          />
        </div>
        {saveM.isError && (
          <p role="alert" data-testid="scim-save-error" className="text-sm text-danger">
            {apiErrorMessage(saveM.error, t("admin.identity.saveError"))}
          </p>
        )}

        <div>
          <div className="mb-1 text-xs font-medium text-muted">{t("admin.identity.scim.baseUrl")}</div>
          {status.base_url ? (
            <div className="flex flex-wrap items-center gap-2">
              <code
                data-testid="scim-base-url"
                className="break-all rounded border border-hairline bg-surface-subtle px-2 py-1 font-mono text-[12.5px] text-ink"
              >
                {status.base_url}
              </code>
              <CopyButton text={status.base_url} testId="scim-base-url-copy" />
            </div>
          ) : (
            <p data-testid="scim-base-url-missing" className="text-sm text-escalation">
              {t("admin.identity.scim.baseUrlMissing")}
            </p>
          )}
        </div>

        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm" data-testid="scim-counts">
          {(
            [
              ["users", status.users],
              ["active", status.active_users],
              ["groups", status.groups],
              ["pending", status.pending_sync],
            ] as const
          ).map(([k, n]) => (
            <div key={k} className="flex items-baseline gap-1.5">
              <dt className="text-xs text-muted">{t(`admin.identity.scim.counts.${k}`)}</dt>
              <dd data-testid={`scim-count-${k}`} className="font-medium text-ink">
                {n}
              </dd>
            </div>
          ))}
          <div className="flex items-baseline gap-1.5">
            <dt className="text-xs text-muted">{t("admin.identity.scim.counts.lastActivity")}</dt>
            <dd data-testid="scim-last-activity" className="font-medium text-ink">
              {status.last_activity_at
                ? formatDateTime(status.last_activity_at, locale)
                : t("admin.identity.scim.tokens.never")}
            </dd>
          </div>
        </dl>
      </section>

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="scim-tokens">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink">{t("admin.identity.scim.tokens.title")}</h2>
          <Button type="button" variant="primary" data-testid="scim-token-new" onClick={() => setDialogOpen(true)}>
            {t("admin.identity.scim.tokens.create")}
          </Button>
        </div>
        {revokeM.isError && (
          <p role="alert" className="text-sm text-danger">
            {apiErrorMessage(revokeM.error, t("admin.identity.scim.tokens.revokeError"))}
          </p>
        )}
        {status.tokens.length === 0 ? (
          <p data-testid="scim-tokens-empty" className="text-sm text-muted">
            {t("admin.identity.scim.tokens.empty")}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-xs text-muted">
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.tokens.name")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.tokens.userKind")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.tokens.hint")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.tokens.expires")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.tokens.lastUsed")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.tokens.status")}</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {status.tokens.map((tok) => (
                  <tr key={tok.id} className="border-t border-hairline" data-testid="scim-token-row">
                    <td className="px-3 py-2 text-ink">{tok.name}</td>
                    <td className="px-3 py-2 text-ink">{t(`admin.identity.scim.kinds.${tok.user_kind}`)}</td>
                    <td className="px-3 py-2 font-mono text-[12.5px] text-muted">…{tok.token_hint}</td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(tok.expires_at, locale)}</td>
                    <td className="px-3 py-2 text-muted">
                      {tok.last_used_at ? formatDateTime(tok.last_used_at, locale) : t("admin.identity.scim.tokens.never")}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={tok.active ? "success" : "muted"}>
                        {tok.active
                          ? t("admin.identity.scim.tokens.activeState")
                          : tok.revoked_at
                            ? t("admin.identity.scim.tokens.revoked")
                            : t("admin.identity.scim.tokens.expired")}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-right">
                      {tok.active && (
                        <Button
                          type="button"
                          size="sm"
                          data-testid={`scim-token-revoke-${tok.id}`}
                          disabled={revokeM.isPending}
                          onClick={() => onRevoke(tok)}
                        >
                          {t("admin.identity.scim.tokens.revoke")}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="scim-accounts">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink">{t("admin.identity.scim.accounts.title")}</h2>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input
                type="checkbox"
                data-testid="scim-accounts-show-deleted"
                checked={showDeleted}
                onChange={(e) => {
                  setShowDeleted(e.target.checked);
                  setAccountOffset(0);
                }}
              />
              {t("admin.identity.scim.accounts.showDeleted")}
            </label>
            <input
              type="search"
              data-testid="scim-accounts-search"
              aria-label={t("admin.identity.scim.accounts.search")}
              placeholder={t("admin.identity.scim.accounts.search")}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setAccountOffset(0);
              }}
              className="w-56 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
        </div>
        {accountsQ.isLoading && <Spinner />}
        {accountsQ.isError && (
          <p role="alert" className="text-sm text-danger">
            {apiErrorMessage(accountsQ.error, t("admin.identity.scim.loadError"))}
          </p>
        )}
        {accountsQ.data && accountsQ.data.items.length === 0 && (
          <p data-testid="scim-accounts-empty" className="text-sm text-muted">
            {t("admin.identity.scim.accounts.empty")}
          </p>
        )}
        {accountsQ.data && accountsQ.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-xs text-muted">
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.accounts.login")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.tokens.userKind")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.accounts.userName")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.accounts.externalId")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.tokens.status")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.accounts.changed")}</th>
                </tr>
              </thead>
              <tbody>
                {accountsQ.data.items.map((a) => (
                  <tr key={a.scim_id} className="border-t border-hairline" data-testid="scim-account-row">
                    <td className="px-3 py-2 text-ink">{a.login ?? "—"}</td>
                    <td className="px-3 py-2 text-ink">{t(`admin.identity.links.kinds.${a.user_kind}`)}</td>
                    <td className="break-all px-3 py-2 text-ink">{a.user_name}</td>
                    <td className="break-all px-3 py-2 font-mono text-[12.5px] text-muted">{a.external_id ?? "—"}</td>
                    <td className="px-3 py-2">
                      <Badge tone={a.deleted ? "muted" : a.active ? "success" : "warn"}>
                        {a.deleted
                          ? t("admin.identity.scim.accounts.deleted")
                          : a.active
                            ? t("admin.identity.scim.accounts.active")
                            : t("admin.identity.scim.accounts.inactive")}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(a.changed, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {accountsQ.data && (
          <Pager testId="scim-accounts-pager" offset={accountOffset} total={accountsQ.data.total} onChange={setAccountOffset} />
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="scim-groups">
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.scim.groups.title")}</h2>
        <p className="text-xs text-muted">{t("admin.identity.scim.groups.rulesHint")}</p>
        {groupsQ.isLoading && <Spinner />}
        {groupsQ.isError && (
          <p role="alert" className="text-sm text-danger">
            {apiErrorMessage(groupsQ.error, t("admin.identity.scim.loadError"))}
          </p>
        )}
        {groupsQ.data && groupsQ.data.items.length === 0 && (
          <p data-testid="scim-groups-empty" className="text-sm text-muted">
            {t("admin.identity.scim.groups.empty")}
          </p>
        )}
        {groupsQ.data && groupsQ.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-xs text-muted">
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.groups.name")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.accounts.externalId")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.groups.members")}</th>
                  <th className="px-3 py-2 font-medium">{t("admin.identity.scim.accounts.changed")}</th>
                </tr>
              </thead>
              <tbody>
                {groupsQ.data.items.map((g) => (
                  <tr key={g.scim_id} className="border-t border-hairline" data-testid="scim-group-row">
                    <td className="px-3 py-2 text-ink">{g.display_name}</td>
                    <td className="break-all px-3 py-2 font-mono text-[12.5px] text-muted">{g.external_id ?? "—"}</td>
                    <td className="px-3 py-2 text-ink">{g.member_count}</td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(g.changed, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {groupsQ.data && (
          <Pager testId="scim-groups-pager" offset={groupOffset} total={groupsQ.data.total} onChange={setGroupOffset} />
        )}
      </section>

      <ScimEventsList providerId={id} />

      <ScimTokenDialog
        open={dialogOpen}
        providerId={id}
        audience={provider.audience}
        onClose={() => setDialogOpen(false)}
        onCreated={() => qc.invalidateQueries({ queryKey: key })}
      />
      {dialog}
    </div>
  );
}
