import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  type AuthConfigAgentOut,
  type AuthConfigGlobalOut,
  type GroupOut,
} from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";

const LIST_KEY = ["admin", "auth-config"] as const;
const GLOBAL_KEY = ["admin", "auth-config", "global"] as const;
const GROUPS_KEY = ["admin", "groups", "for-auth-config"] as const;

export function AuthConfigPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [resetTarget, setResetTarget] = useState<AuthConfigAgentOut | null>(null);
  const [globalDraft, setGlobalDraft] = useState<AuthConfigGlobalOut | null>(null);
  const [globalMsg, setGlobalMsg] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: LIST_KEY,
    queryFn: ({ signal }) =>
      api.adminAuthConfig.list({ valid: "valid", pageSize: 500 }, signal),
  });

  const globalQ = useQuery({
    queryKey: GLOBAL_KEY,
    queryFn: ({ signal }) => api.adminAuthConfig.getGlobal(signal),
  });

  const groupsQ = useQuery({
    queryKey: GROUPS_KEY,
    queryFn: ({ signal }) =>
      api.adminGroups.list({ valid: "valid", pageSize: 500 }, signal),
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    if (globalQ.data) {
      setGlobalDraft({
        enforce_all: globalQ.data.enforce_all,
        enforce_group_ids: [...(globalQ.data.enforce_group_ids ?? [])],
      });
    }
  }, [globalQ.data]);

  const invalidateList = () => qc.invalidateQueries({ queryKey: LIST_KEY });

  const updateM = useMutation({
    mutationFn: ({
      userId,
      body,
    }: {
      userId: number;
      body: { sso_eligible?: boolean; enforce_2fa?: boolean };
    }) => api.adminAuthConfig.update(userId, body),
    onSuccess: async () => {
      await invalidateList();
    },
  });

  const resetM = useMutation({
    mutationFn: (userId: number) => api.adminAuthConfig.reset2fa(userId),
    onSuccess: async () => {
      setResetTarget(null);
      await invalidateList();
    },
  });

  const globalM = useMutation({
    mutationFn: (body: AuthConfigGlobalOut) =>
      api.adminAuthConfig.putGlobal({
        enforce_all: body.enforce_all,
        enforce_group_ids: body.enforce_group_ids,
      }),
    onSuccess: (data) => {
      qc.setQueryData(GLOBAL_KEY, data);
      setGlobalDraft({
        enforce_all: data.enforce_all,
        enforce_group_ids: [...(data.enforce_group_ids ?? [])],
      });
      setGlobalMsg(t("admin.authConfig.globalSaved"));
    },
    onError: () => setGlobalMsg(t("admin.authConfig.globalSaveError")),
  });

  const groups: GroupOut[] = groupsQ.data?.items ?? [];
  const selectedGroupIds = new Set(globalDraft?.enforce_group_ids ?? []);

  const toggleGroup = (groupId: number) => {
    setGlobalDraft((prev) => {
      if (!prev) return prev;
      const set = new Set(prev.enforce_group_ids);
      if (set.has(groupId)) set.delete(groupId);
      else set.add(groupId);
      return { ...prev, enforce_group_ids: Array.from(set).sort((a, b) => a - b) };
    });
  };

  const saveGlobal = () => {
    if (!globalDraft) return;
    setGlobalMsg(null);
    globalM.mutate(globalDraft);
  };

  const columns: DataTableColumn<AuthConfigAgentOut>[] = useMemo(
    () => [
      {
        key: "login",
        header: t("admin.authConfig.login"),
        mono: true,
        render: (r) => r.login,
      },
      {
        key: "name",
        header: t("admin.authConfig.name"),
        render: (r) => r.full_name || "—",
      },
      {
        key: "totp",
        header: t("admin.authConfig.totpStatus"),
        render: (r) => (
          <Badge tone={r.totp_enabled ? "success" : "muted"}>
            {r.totp_enabled
              ? t("admin.authConfig.totpActive")
              : t("admin.authConfig.totpInactive")}
          </Badge>
        ),
      },
      {
        key: "sso",
        header: t("admin.authConfig.ssoEligible"),
        render: (r) => (
          <label className="inline-flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              data-testid={`auth-config-sso-${r.user_id}`}
              checked={r.sso_eligible}
              disabled={updateM.isPending}
              onChange={(e) => {
                updateM.mutate({
                  userId: r.user_id,
                  body: { sso_eligible: e.target.checked },
                });
              }}
              className="rounded border-hairline"
            />
          </label>
        ),
      },
      {
        key: "enforce",
        header: t("admin.authConfig.enforce2fa"),
        render: (r) => (
          <label className="inline-flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              data-testid={`auth-config-enforce-${r.user_id}`}
              checked={r.enforce_2fa}
              disabled={updateM.isPending}
              onChange={(e) => {
                updateM.mutate({
                  userId: r.user_id,
                  body: { enforce_2fa: e.target.checked },
                });
              }}
              className="rounded border-hairline"
            />
          </label>
        ),
      },
      {
        key: "reset",
        header: t("admin.authConfig.reset2fa"),
        render: (r) => (
          <Button
            variant="ghost"
            size="sm"
            data-testid={`auth-config-reset-${r.user_id}`}
            disabled={!r.totp_enabled || resetM.isPending}
            onClick={(e) => {
              e.stopPropagation();
              setResetTarget(r);
            }}
          >
            {t("admin.authConfig.reset2fa")}
          </Button>
        ),
      },
    ],
    [t, updateM, resetM.isPending],
  );

  return (
    <div className="space-y-6 p-4" data-testid="admin-auth-config-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.authConfig.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.authConfig.description")}</p>
      </div>

      <section
        className="space-y-4 rounded-lg border border-hairline bg-surface p-4"
        data-testid="auth-config-global"
      >
        <h2 className="text-sm font-semibold text-ink">{t("admin.authConfig.globalHeading")}</h2>

        {globalQ.isLoading || !globalDraft ? (
          <Spinner />
        ) : (
          <>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="auth-config-enforce-all"
                checked={globalDraft.enforce_all}
                onChange={(e) =>
                  setGlobalDraft((prev) =>
                    prev ? { ...prev, enforce_all: e.target.checked } : prev,
                  )
                }
                className="rounded border-hairline"
              />
              {t("admin.authConfig.enforceAll")}
            </label>

            <div>
              <p className="mb-2 text-sm text-muted">{t("admin.authConfig.enforceGroups")}</p>
              {groupsQ.isLoading ? (
                <Spinner />
              ) : groups.length === 0 ? (
                <p className="text-sm text-muted">{t("admin.authConfig.noGroups")}</p>
              ) : (
                <div
                  className="max-h-48 space-y-1 overflow-y-auto rounded-md border border-hairline p-2"
                  data-testid="auth-config-group-list"
                >
                  {groups.map((g) => (
                    <label
                      key={g.id}
                      className="flex items-center gap-2 rounded px-1 py-0.5 text-sm text-ink hover:bg-surface-subtle"
                    >
                      <input
                        type="checkbox"
                        data-testid={`auth-config-group-${g.id}`}
                        checked={selectedGroupIds.has(g.id)}
                        onChange={() => toggleGroup(g.id)}
                        className="rounded border-hairline"
                      />
                      <span className="font-mono text-xs text-muted">#{g.id}</span>
                      <span>{g.name}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            <div className="flex items-center gap-3">
              <Button
                type="button"
                variant="primary"
                data-testid="auth-config-global-save"
                disabled={globalM.isPending}
                onClick={saveGlobal}
              >
                {globalM.isPending ? <Spinner /> : t("admin.authConfig.saveGlobal")}
              </Button>
              {globalMsg && (
                <p className="text-sm text-muted" data-testid="auth-config-global-msg">
                  {globalMsg}
                </p>
              )}
            </div>
          </>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-ink">
          {t("admin.authConfig.agentsHeading")}
        </h2>
        <DataTable
          testId="auth-config-table"
          columns={columns}
          rows={listQ.data?.items ?? []}
          rowKey={(r) => r.user_id}
          isLoading={listQ.isLoading}
          emptyLabel={t("admin.authConfig.empty")}
        />
      </section>

      <Dialog
        open={resetTarget !== null}
        onClose={() => setResetTarget(null)}
        title={t("admin.authConfig.resetConfirmTitle")}
      >
        <p className="mb-4 text-sm text-muted">
          {t("admin.authConfig.resetConfirmBody", {
            login: resetTarget?.login ?? "",
          })}
        </p>
        <div className="flex justify-end gap-2">
          <Button
            variant="secondary"
            data-testid="auth-config-reset-cancel"
            onClick={() => setResetTarget(null)}
          >
            {t("common.close")}
          </Button>
          <Button
            variant="danger"
            data-testid="auth-config-reset-confirm"
            disabled={resetM.isPending || !resetTarget}
            onClick={() => {
              if (resetTarget) resetM.mutate(resetTarget.user_id);
            }}
          >
            {resetM.isPending ? <Spinner /> : t("admin.authConfig.reset2fa")}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
