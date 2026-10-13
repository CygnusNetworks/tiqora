import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { PriorityMatrixEditor } from "@/components/itsm/PriorityMatrixEditor";
import { api } from "@/lib/api";
import {
  itsmApi,
  itsmErrorCode,
  itsmKeys,
  type ItsmAdmin,
  type ItsmMode,
  type PriorityMatrix,
  type SetupPlan,
} from "@/lib/itsmApi";

const IMPORT_ROUTE = "/admin/itsm-import";
type SetupGroup = SetupPlan["items"][number]["group"];

const STATUS_TONE = { present: "success", missing: "warn", conflict: "danger", invalid: "danger" } as const;

/** Message of a failed admin call; a refused mode change names the reason code. */
function useErrorText() {
  const { t } = useTranslation();
  return (err: unknown): string => {
    const code = itsmErrorCode(err);
    if (code === "mode_transition_not_allowed") {
      const body = (err as { detail?: { detail?: { reason?: string } } }).detail?.detail;
      return t("admin.itsm.mode.transitionRefused", {
        reason: t(`admin.itsm.mode.reasons.${body?.reason ?? "unknown"}`, { defaultValue: body?.reason ?? "" }),
      });
    }
    if (code) return t(`itsm.errors.${code}`, { defaultValue: t("admin.itsm.error") });
    return t("admin.itsm.error");
  };
}

function ModeCard({ admin }: { admin: ItsmAdmin }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const router = useRouter({ warn: false });
  const errorText = useErrorText();
  const switchMode = useMutation({
    mutationFn: (mode: ItsmMode) => itsmApi.admin.setMode(mode),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin", "itsm"] });
      await qc.invalidateQueries({ queryKey: ["itsm"] });
    },
  });
  const importRouteExists = !!(router?.routesByPath as Record<string, unknown> | undefined)?.[IMPORT_ROUTE];
  const packages = Object.entries(admin.detected.packages);

  return (
    <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="itsm-mode-card">
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold text-ink">{t("admin.itsm.mode.title")}</h2>
        <Badge tone="accent" data-testid="itsm-mode-current">
          {t(`admin.itsm.mode.names.${admin.mode}`)}
        </Badge>
        <span className="text-xs text-muted" data-testid="itsm-mode-source">
          {t(`admin.itsm.mode.sources.${admin.source}`)}
        </span>
      </header>
      <p className="text-sm text-muted">{t(`admin.itsm.mode.descriptions.${admin.mode}`)}</p>

      <div className="text-sm" data-testid="itsm-detected">
        <span className="font-medium text-ink">{t("admin.itsm.mode.detected")}: </span>
        {admin.detected.core_installed ? (
          <span className="text-muted">
            {packages.map(([name, version]) => `${name} ${version}`).join(", ") || t("admin.itsm.mode.detectedCore")}
          </span>
        ) : (
          <span className="text-muted">{t("admin.itsm.mode.detectedNone")}</span>
        )}
      </div>
      {admin.detected.unsupported_reason && (
        <p className="text-sm text-escalation" data-testid="itsm-unsupported">
          {t("admin.itsm.mode.unsupported", { reason: admin.detected.unsupported_reason })}
        </p>
      )}

      {admin.mode === "znuny_readthrough" && (
        <p className="text-sm text-muted" data-testid="itsm-mode-a-note">
          {t("admin.itsm.mode.importHint")}{" "}
          {importRouteExists && (
            <Link to={IMPORT_ROUTE as never} className="text-accent underline" data-testid="itsm-import-link">
              {t("admin.itsm.mode.importLink")}
            </Link>
          )}
        </p>
      )}

      <div className="flex flex-wrap gap-2" data-testid="itsm-transitions">
        {admin.allowed_transitions.map((mode) => (
          <Button
            key={mode}
            data-testid={`itsm-switch-${mode}`}
            disabled={switchMode.isPending}
            onClick={() => switchMode.mutate(mode)}
          >
            {t(`admin.itsm.mode.switchTo.${mode}`)}
          </Button>
        ))}
      </div>
      {switchMode.isError && (
        <p role="alert" className="text-sm text-danger" data-testid="itsm-mode-error">
          {errorText(switchMode.error)}
        </p>
      )}
    </section>
  );
}

function SetupCard() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const errorText = useErrorText();
  const plan = useQuery({ queryKey: itsmKeys.adminSetup(), queryFn: ({ signal }) => itsmApi.admin.setup(signal) });
  const [picked, setPicked] = useState<Set<SetupGroup>>(new Set(["incident_problem"]));
  const apply = useMutation({
    mutationFn: () => itsmApi.admin.applySetup({ groups: [...picked] }),
    onSuccess: async (data) => {
      qc.setQueryData(itsmKeys.adminSetup(), data);
      await qc.invalidateQueries({ queryKey: itsmKeys.status() });
    },
  });

  const items = plan.data?.items ?? [];
  const groups: SetupGroup[] = ["incident_problem", "change"];
  const toggle = (g: SetupGroup, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(g);
      else next.delete(g);
      return next;
    });

  return (
    <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="itsm-setup-card">
      <h2 className="text-base font-semibold text-ink">{t("admin.itsm.setup.title")}</h2>
      <p className="text-sm text-muted">{t("admin.itsm.setup.intro")}</p>
      {plan.isPending && <Spinner />}
      {plan.isError && (
        <p role="alert" className="text-sm text-danger">
          {errorText(plan.error)}
        </p>
      )}
      {plan.data &&
        groups.map((group) => {
          const rows = items.filter((i) => i.group === group);
          if (rows.length === 0) return null;
          return (
            <div key={group} className="space-y-1" data-testid={`itsm-setup-group-${group}`}>
              <label className="flex items-center gap-2 text-sm font-medium text-ink">
                <input
                  type="checkbox"
                  checked={picked.has(group)}
                  onChange={(e) => toggle(group, e.target.checked)}
                  data-testid={`itsm-setup-check-${group}`}
                />
                {t(`admin.itsm.setup.groups.${group}`)}
              </label>
              <ul className="divide-y divide-hairline rounded border border-hairline text-sm">
                {rows.map((row) => (
                  <li key={`${row.kind}:${row.name}`} className="flex items-center gap-2 px-2 py-1.5">
                    <span className="w-32 shrink-0 text-xs text-muted">{t(`admin.itsm.setup.kinds.${row.kind}`)}</span>
                    <span className="flex-1 text-ink">{row.name}</span>
                    <Badge tone={STATUS_TONE[row.status]} data-testid={`itsm-setup-status-${row.kind}-${row.name}`}>
                      {t(`admin.itsm.setup.status.${row.status}`)}
                    </Badge>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      {plan.data && (
        <div className="flex items-center gap-3">
          <Button
            variant="primary"
            data-testid="itsm-setup-apply"
            disabled={apply.isPending || picked.size === 0}
            onClick={() => apply.mutate()}
          >
            {t("admin.itsm.setup.apply")}
          </Button>
          {plan.data.complete && <span className="text-sm text-muted">{t("admin.itsm.setup.complete")}</span>}
        </div>
      )}
      {apply.isError && (
        <p role="alert" className="text-sm text-danger">
          {errorText(apply.error)}
        </p>
      )}
    </section>
  );
}

function MatrixCard() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const errorText = useErrorText();
  const matrix = useQuery({
    queryKey: itsmKeys.adminMatrix(),
    queryFn: ({ signal }) => itsmApi.admin.priorityMatrix(signal),
  });
  const priorities = useQuery({
    queryKey: ["admin", "itsm", "priorities"],
    queryFn: ({ signal }) => api.adminPriorities.list({ page: 1, pageSize: 500 }, signal),
  });
  const save = useMutation({
    mutationFn: (cells: PriorityMatrix["cells"]) => itsmApi.admin.savePriorityMatrix({ cells }),
    onSuccess: (data) => {
      qc.setQueryData(itsmKeys.adminMatrix(), data);
      void qc.invalidateQueries({ queryKey: itsmKeys.matrix() });
    },
  });

  return (
    <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="itsm-matrix-card">
      <h2 className="text-base font-semibold text-ink">{t("admin.itsm.matrix.title")}</h2>
      {matrix.isPending && <Spinner />}
      {matrix.isError && (
        <p role="alert" className="text-sm text-danger">
          {errorText(matrix.error)}
        </p>
      )}
      {matrix.data && (
        <PriorityMatrixEditor
          matrix={matrix.data}
          priorities={priorities.data?.items ?? []}
          saving={save.isPending}
          error={save.isError ? errorText(save.error) : null}
          onSave={(cells) => save.mutate(cells)}
        />
      )}
    </section>
  );
}

/** `/admin/itsm`: mode, setup, priority matrix. */
export function ItsmPage() {
  const { t } = useTranslation();
  const admin = useQuery({ queryKey: itsmKeys.admin(), queryFn: ({ signal }) => itsmApi.admin.get(signal) });
  const errorText = useErrorText();
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-itsm-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("admin.itsm.title")}</h1>
      {admin.isPending && <Spinner />}
      {admin.isError && (
        <p role="alert" className="text-sm text-danger">
          {errorText(admin.error)}
        </p>
      )}
      {admin.data && (
        <>
          <ModeCard admin={admin.data} />
          {admin.data.mode !== "off" && (
            <>
              <SetupCard />
              <MatrixCard />
            </>
          )}
        </>
      )}
    </div>
  );
}
