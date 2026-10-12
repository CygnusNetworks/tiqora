import { useState } from "react";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { Tabs } from "@/components/ui/Tabs";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  WEBSERVICE_TABS,
  webservicesApi,
  type WebserviceTab,
  type WebserviceDetail,
  type WebserviceInvoker,
} from "@/lib/webservicesApi";
import { DryRunDialog } from "./gi/DryRunDialog";
import { GiNoticeBanner } from "./WebservicesPage";

function yesNo(t: (k: string) => string, value: boolean): string {
  return value ? t("admin.webservices.yes") : t("admin.webservices.no");
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-hairline bg-surface p-4">
      <h2 className="mb-2 text-sm font-semibold text-ink">{title}</h2>
      {children}
    </section>
  );
}

function OverviewTab({ ws }: { ws: WebserviceDetail }) {
  const { t } = useTranslation();
  const transport = ws.requester_transport;
  return (
    <div className="space-y-3" data-testid="ws-tab-overview">
      {ws.problem && (
        <p className="rounded-lg border border-danger/30 bg-danger/10 p-2 text-sm text-danger" data-testid="ws-problem">
          {ws.problem}
        </p>
      )}
      {ws.warnings.length > 0 && (
        <ul className="space-y-1 rounded-xl border border-amber/30 bg-amber/10 p-3 text-sm text-ink" data-testid="ws-warnings">
          {ws.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <Section title={t("admin.webservices.overview.transport")}>
        {transport ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted">{t("admin.webservices.overview.type")}</dt>
            <dd className="font-mono text-ink">{transport.type}</dd>
            <dt className="text-muted">{t("admin.webservices.overview.endpoint")}</dt>
            <dd className="break-all font-mono text-ink">{transport.endpoint ?? "—"}</dd>
            <dt className="text-muted">{t("admin.webservices.overview.auth")}</dt>
            <dd className="text-ink">{transport.auth_type ?? t("admin.webservices.overview.noAuth")}</dd>
            <dt className="text-muted">{t("admin.webservices.overview.proxy")}</dt>
            <dd className="text-ink">{yesNo(t, transport.has_proxy)}</dd>
            <dt className="text-muted">{t("admin.webservices.overview.clientCert")}</dt>
            <dd className="text-ink">{yesNo(t, transport.uses_client_certificate)}</dd>
            <dt className="text-muted">{t("admin.webservices.overview.insecureTls")}</dt>
            <dd className="text-ink">{yesNo(t, transport.insecure_tls_requested)}</dd>
          </dl>
        ) : (
          <p className="text-sm text-muted">{t("admin.webservices.overview.noTransport")}</p>
        )}
      </Section>
      <Section title={t("admin.webservices.overview.errorHandling")}>
        {ws.error_handling.length === 0 ? (
          <p className="text-sm text-muted">{t("admin.webservices.overview.noErrorHandling")}</p>
        ) : (
          <ul className="space-y-1 text-sm text-ink">
            {ws.error_handling.map((eh) => (
              <li key={eh.key}>
                <span className="font-semibold">{eh.key}</span>{" "}
                <span className="text-muted">({eh.type})</span>
                {eh.retry_summary && <span className="text-muted"> — {eh.retry_summary}</span>}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-muted">
          {t("admin.webservices.overview.debugThreshold", { level: ws.debug_threshold })}
        </p>
      </Section>
    </div>
  );
}

function InvokerCard({
  invoker,
  onDryRun,
}: {
  invoker: WebserviceInvoker;
  onDryRun: (invoker: WebserviceInvoker) => void;
}) {
  const { t } = useTranslation();
  return (
    <li
      className="rounded-xl border border-hairline bg-surface p-4"
      data-testid={`invoker-${invoker.name}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="font-semibold text-ink">{invoker.name}</span>{" "}
          <span className="font-mono text-xs text-muted">{invoker.type}</span>
        </div>
        <Button
          size="sm"
          variant="secondary"
          disabled={!invoker.supported}
          onClick={() => onDryRun(invoker)}
          data-testid={`invoker-dryrun-${invoker.name}`}
        >
          {t("admin.webservices.invokers.dryRun")}
        </Button>
      </div>
      {invoker.problem && (
        <p className="mt-2 text-xs text-danger" data-testid={`invoker-problem-${invoker.name}`}>
          {invoker.problem}
        </p>
      )}
      <p className="mt-2 text-xs text-muted">
        {t("admin.webservices.invokers.mappings", {
          outbound: invoker.mapping_outbound ?? "—",
          inbound: invoker.mapping_inbound ?? "—",
        })}
      </p>
      {invoker.events.length === 0 ? (
        <p className="mt-2 text-xs text-muted">{t("admin.webservices.invokers.noEvents")}</p>
      ) : (
        <ul className="mt-2 flex flex-wrap gap-2">
          {invoker.events.map((ev) => (
            <li
              key={ev.event}
              className="flex flex-wrap items-center gap-1 rounded-lg border border-hairline bg-surface-subtle px-2 py-1"
              data-testid={`event-${invoker.name}-${ev.event}`}
            >
              <span className="font-mono text-xs text-ink">{ev.event}</span>
              <Badge tone="muted">
                {ev.asynchronous
                  ? t("admin.webservices.invokers.background")
                  : t("admin.webservices.invokers.immediate")}
              </Badge>
              {ev.has_condition && <Badge tone="accent">{t("admin.webservices.invokers.condition")}</Badge>}
              {!ev.fired_by_tiqora && <Badge tone="warn">{t("admin.webservices.invokers.notFired")}</Badge>}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function InvokersTab({ ws }: { ws: WebserviceDetail }) {
  const { t } = useTranslation();
  const [dryRun, setDryRun] = useState<WebserviceInvoker | null>(null);
  return (
    <div className="space-y-3" data-testid="ws-tab-invokers">
      <p className="text-sm text-muted">{t("admin.webservices.invokers.description")}</p>
      {ws.invokers.length === 0 ? (
        <p className="text-sm text-muted" data-testid="ws-no-invokers">
          {t("admin.webservices.invokers.none")}
        </p>
      ) : (
        <ul className="space-y-3">
          {ws.invokers.map((inv) => (
            <InvokerCard key={inv.name} invoker={inv} onDryRun={setDryRun} />
          ))}
        </ul>
      )}
      {dryRun && (
        <DryRunDialog
          wsId={ws.id}
          invoker={dryRun.name}
          events={dryRun.events.map((e) => e.event)}
          onClose={() => setDryRun(null)}
        />
      )}
    </div>
  );
}

function SettingsTab({ ws }: { ws: WebserviceDetail }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [targets, setTargets] = useState<string | null>(null);
  const [insecure, setInsecure] = useState<boolean | null>(null);
  const [saved, setSaved] = useState(false);

  const targetsText = targets ?? ws.egress.allowed_targets.join("\n");
  const insecureValue = insecure ?? ws.egress.allow_insecure_tls;
  const dirty = targets !== null || insecure !== null;

  const save = useMutation({
    mutationFn: () =>
      webservicesApi.putEgress(ws.id, {
        allowed_targets: targetsText
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
        allow_insecure_tls: insecureValue,
      }),
    onSuccess: (egress) => {
      qc.setQueryData<WebserviceDetail>(["admin", "webservices", ws.id], (prev) =>
        prev ? { ...prev, egress } : prev,
      );
      setTargets(null);
      setInsecure(null);
      setSaved(true);
    },
  });

  return (
    <form
      className="space-y-4"
      data-testid="ws-tab-settings"
      onSubmit={(e) => {
        e.preventDefault();
        setSaved(false);
        save.mutate();
      }}
    >
      <Section title={t("admin.webservices.settings.targetsTitle")}>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-ink">
            {t("admin.webservices.settings.targets")}
          </span>
          <textarea
            rows={4}
            value={targetsText}
            onChange={(e) => {
              setTargets(e.target.value);
              setSaved(false);
            }}
            className="w-full rounded-lg border border-hairline bg-surface px-2 py-1.5 font-mono text-xs text-ink"
            aria-describedby="ws-targets-help"
            data-testid="ws-targets"
          />
        </label>
        <p id="ws-targets-help" className="mt-1 text-xs text-muted">
          {t("admin.webservices.settings.targetsHelp")}
        </p>
      </Section>
      <Section title={t("admin.webservices.settings.tlsTitle")}>
        <div className="flex items-center gap-3">
          <Switch
            checked={insecureValue}
            onChange={(v) => {
              setInsecure(v);
              setSaved(false);
            }}
            aria-label={t("admin.webservices.settings.insecureTls")}
            aria-describedby="ws-tls-warning"
            testId="ws-insecure-tls"
          />
          <span className="text-sm font-medium text-ink">
            {t("admin.webservices.settings.insecureTls")}
          </span>
        </div>
        <p id="ws-tls-warning" className="mt-2 text-xs text-amber" data-testid="ws-tls-warning">
          {t("admin.webservices.settings.insecureTlsWarning")}
        </p>
      </Section>
      {save.isError && (
        <p className="text-sm text-danger" role="alert" data-testid="ws-egress-error">
          {apiErrorMessage(save.error, t("admin.webservices.settings.saveFailed"))}
        </p>
      )}
      {saved && (
        <p className="text-sm text-green" role="status" data-testid="ws-egress-saved">
          {t("admin.webservices.settings.saved")}
        </p>
      )}
      <Button type="submit" variant="primary" disabled={!dirty || save.isPending} data-testid="ws-egress-save">
        {t("admin.webservices.settings.save")}
      </Button>
    </form>
  );
}

export function WebserviceDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { wsId: rawId } = useParams({ from: "/admin/webservices/$wsId" });
  const search = useSearch({ from: "/admin/webservices/$wsId" });
  const wsId = Number(rawId);
  const tab: WebserviceTab = WEBSERVICE_TABS.includes(search.tab as WebserviceTab)
    ? (search.tab as WebserviceTab)
    : "overview";

  const detailQ = useQuery({
    queryKey: ["admin", "webservices", wsId],
    queryFn: ({ signal }) => webservicesApi.get(wsId, signal),
    enabled: Number.isSafeInteger(wsId),
  });

  if (detailQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-webservice-detail">
        <Spinner />
      </div>
    );
  }
  if (detailQ.isError || !detailQ.data) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-webservice-detail">
        {t("admin.webservices.detailLoadError")}
      </div>
    );
  }
  const ws = detailQ.data;

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-webservice-detail">
      <div>
        <Link to="/admin/webservices" className="text-xs font-medium text-accent hover:underline">
          {t("admin.webservices.back")}
        </Link>
        <h1 className="mt-1 flex items-center gap-2 font-display text-xl font-semibold text-ink">
          {ws.name}
          <Badge tone={ws.valid ? "success" : "danger"}>
            {ws.valid ? t("admin.webservices.valid") : t("admin.webservices.invalid")}
          </Badge>
        </h1>
      </div>
      <GiNoticeBanner />
      <Tabs
        items={[
          { id: "overview", label: t("admin.webservices.tabs.overview") },
          { id: "invokers", label: t("admin.webservices.tabs.invokers"), count: ws.invokers.length },
          { id: "debugger", label: t("admin.webservices.tabs.debugger") },
          { id: "settings", label: t("admin.webservices.tabs.settings") },
        ]}
        value={tab}
        onChange={(id) =>
          void navigate({
            to: "/admin/webservices/$wsId",
            params: { wsId: String(wsId) },
            search: { tab: id as WebserviceTab },
            replace: true,
          })
        }
      />
      {tab === "overview" && <OverviewTab ws={ws} />}
      {tab === "invokers" && <InvokersTab ws={ws} />}
      {tab === "debugger" && (
        <p className="text-sm text-muted" data-testid="ws-tab-debugger">
          {t("admin.webservices.debugger.placeholder")}
        </p>
      )}
      {tab === "settings" && <SettingsTab ws={ws} />}
    </div>
  );
}
