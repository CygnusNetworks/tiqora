import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ChangeTypeBadge } from "@/components/itsm/change/ChangeTypeBadge";
import { ChangeLinkRow } from "@/components/itsm/change/ChangeLinkRow";
import { CreateChangeDialog } from "@/components/itsm/change/CreateChangeDialog";
import { RiskBadge } from "@/components/itsm/change/RiskBadge";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { api } from "@/lib/api";
import {
  changeApi,
  changeErrorCode,
  changeKeys,
  type ChangeListItem,
  type ChangeListQuery,
  type ChangePhase,
  type ChangeTaskListItem,
  type ChangeType,
} from "@/lib/changeApi";
import { CHANGE_PHASES, CHANGE_TYPES } from "@/lib/changePhases";
import { changeDetailHref } from "@/lib/itsmRoutes";
import { PHASE_TONE, useChangeStatus } from "@/components/itsm/change/changeUi";

const PAGE = 25;
const DEBOUNCE_MS = 300;
const FIELD_CLASS =
  "w-full rounded-lg border border-hairline bg-surface px-3 py-1.5 text-sm text-ink focus-visible:border-accent focus-visible:outline-none";

type Tab = "all" | "mine" | "tasks" | "approval";
const TABS: Tab[] = ["all", "mine", "tasks", "approval"];

/** Whether two list queries differ at most in the page (`offset`). */
const sameFilter = (a: ChangeListQuery, b: ChangeListQuery) => JSON.stringify({ ...a, offset: 0 }) === JSON.stringify({ ...b, offset: 0 });

/** `YYYY-MM-DD` of a date input as the UTC start (or last second) of that day. */
const dayStart = (d: string) => (d ? `${d}T00:00:00Z` : undefined);
const dayEnd = (d: string) => (d ? `${d}T23:59:59Z` : undefined);

const windowText = (start: string | null, end: string | null) =>
  start && end ? `${start.slice(0, 16).replace("T", " ")} – ${end.slice(0, 16).replace("T", " ")}` : "";

/**
 * `/agent/itsm/changes`: all changes the agent may read, "Meine Changes" (owned), "Meine Aufgaben"
 * (change tasks owned by the agent) and "Freigabe" (changes waiting for the CAB). Times are UTC.
 */
export function ChangeListPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("all");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [phase, setPhase] = useState<ChangePhase | "">("");
  const [type, setType] = useState<ChangeType | "">("");
  const [serviceId, setServiceId] = useState<number | "">("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [openOnly, setOpenOnly] = useState(true);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(text.trim());
      setOffset(0);
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const servicesQ = useQuery({ queryKey: ["reference", "services"], queryFn: () => api.listReferenceServices() });
  const services = useMemo(() => servicesQ.data ?? [], [servicesQ.data]);

  const onTasks = tab === "tasks";
  const query: ChangeListQuery = {
    q: q || undefined,
    // The approval tab is the phase filter fixed to "Freigabe".
    phase: tab === "approval" ? ["approval"] : phase ? [phase] : undefined,
    change_type: type ? [type] : undefined,
    service_id: serviceId === "" ? undefined : serviceId,
    window_start: dayStart(from),
    window_end: dayEnd(to),
    mine: tab === "mine" ? "owned" : undefined,
    order: "updated",
    limit: PAGE,
    offset,
  };
  const listQ = useQuery({
    queryKey: changeKeys.list(query),
    queryFn: ({ signal }) => changeApi.list(query, signal),
    enabled: !onTasks,
    retry: false,
    // Keep the old rows only while paging: another tab or filter must not show rows of the previous one.
    placeholderData: (prev, prevQuery) => (prevQuery && sameFilter(prevQuery.queryKey[3] as ChangeListQuery, query) ? prev : undefined),
  });
  const tasksQ = useQuery({
    queryKey: changeKeys.myTasks(openOnly),
    queryFn: ({ signal }) => changeApi.myTasks(openOnly, signal),
    enabled: onTasks,
    retry: false,
  });
  const active = onTasks ? tasksQ : listQ;
  const errorCode = active.isError ? changeErrorCode(active.error) : null;
  const canLegacy = useChangeStatus().data?.can_see_legacy === true;

  const reset = <T,>(setter: (v: T) => void) => (v: T) => {
    setter(v);
    setOffset(0);
  };
  const serviceName = useMemo(() => new Map(services.map((s) => [s.id, s.name])), [services]);

  const items = listQ.data?.items ?? [];
  const total = listQ.data?.total ?? 0;
  const truncated = listQ.data?.truncated ?? false;
  const tasks = tasksQ.data?.items ?? [];

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6" data-testid="changeListPage">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">{t("itsm.change.pages.list")}</h1>
        <Button variant="primary" size="sm" onClick={() => setCreating(true)} data-testid="change-list-create">
          {t("itsm.change.list.create")}
        </Button>
      </div>
      <ChangeLinkRow current="list" />

      <Tabs
        ariaLabel={t("itsm.change.pages.list")}
        value={tab}
        onChange={(id) => {
          setTab(id as Tab);
          setOffset(0);
        }}
        items={TABS.map((id) => ({ id, label: t(`itsm.change.list.tabs.${id}`) }))}
      />

      {onTasks ? (
        <label className="flex items-center gap-2 text-xs text-muted">
          <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} data-testid="change-list-open-only" />
          {t("itsm.change.list.openOnly")}
        </label>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted" htmlFor="change-list-search">
              {t("itsm.change.list.search")}
            </label>
            <input
              id="change-list-search"
              data-testid="change-list-search"
              type="search"
              className={FIELD_CLASS}
              value={text}
              placeholder={t("itsm.change.list.searchPlaceholder")}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          {tab !== "approval" && (
            <Filter label={t("itsm.change.list.phase")}>
              <SelectField
                items={[
                  { value: "" as ChangePhase | "", label: t("itsm.change.list.allPhases") },
                  ...CHANGE_PHASES.map((p) => ({ value: p as ChangePhase | "", label: t(`itsm.change.phases.${p}`) })),
                ]}
                value={phase}
                onChange={reset(setPhase)}
                testId="change-list-phase"
              />
            </Filter>
          )}
          <Filter label={t("itsm.change.list.type")}>
            <SelectField
              items={[
                { value: "" as ChangeType | "", label: t("itsm.change.list.allTypes") },
                ...CHANGE_TYPES.map((v) => ({ value: v as ChangeType | "", label: t(`itsm.change.types.${v}`) })),
              ]}
              value={type}
              onChange={reset(setType)}
              testId="change-list-type"
            />
          </Filter>
          <Filter label={t("itsm.change.list.service")}>
            <SelectField
              items={[{ value: "" as number | "", label: t("itsm.change.list.allServices") }, ...services.map((s) => ({ value: s.id as number | "", label: s.name }))]}
              value={serviceId}
              onChange={reset(setServiceId)}
              testId="change-list-service"
            />
          </Filter>
          <Filter label={t("itsm.change.list.windowFrom")} htmlFor="change-list-from">
            <input id="change-list-from" data-testid="change-list-from" type="date" className={FIELD_CLASS} value={from} onChange={(e) => reset(setFrom)(e.target.value)} />
          </Filter>
          <Filter label={t("itsm.change.list.windowTo")} htmlFor="change-list-to">
            <input id="change-list-to" data-testid="change-list-to" type="date" className={FIELD_CLASS} value={to} onChange={(e) => reset(setTo)(e.target.value)} />
          </Filter>
        </div>
      )}

      {!onTasks && from && to && from > to && (
        <p className="text-xs text-danger" role="alert" data-testid="change-list-window-invalid">
          {t("itsm.change.list.windowInvalid")}
        </p>
      )}

      {active.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : active.isError ? (
        <div className="rounded-lg border border-hairline bg-surface px-4 py-6 text-center text-sm text-danger" role="alert" data-testid="change-list-error" data-code={errorCode ?? undefined}>
          <p>{t([`itsm.change.errors.${errorCode}`, "itsm.change.errors.generic"])}</p>
          {errorCode === "change_mode_a" && canLegacy && (
            <RouterAnchor href="/agent/itsm/legacy-changes" className="mt-2 inline-block text-accent hover:underline">
              {t("itsm.change.pages.legacyList")}
            </RouterAnchor>
          )}
        </div>
      ) : onTasks ? (
        <>
          <TaskTable tasks={tasks} />
          {tasksQ.data?.truncated && (
            <p className="text-xs text-muted" role="status" data-testid="change-tasks-truncated">
              {t("itsm.change.list.tasksTruncated")}
            </p>
          )}
        </>
      ) : items.length === 0 ? (
        <p className="rounded-lg border border-hairline bg-surface px-4 py-8 text-center text-sm text-muted" data-testid="change-list-empty">
          {t("itsm.change.list.empty")}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
          <table className="w-full text-sm" data-testid="change-list-table">
            <thead className="border-b border-hairline text-left text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">{t("itsm.change.list.colChange")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.change.list.colType")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.change.list.colPhase")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.change.risk.label")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.change.list.colWindow")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.change.list.colServices")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {items.map((c) => (
                <ChangeRow key={c.ticket_id} change={c} serviceName={serviceName} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!onTasks && listQ.data?.truncated && (
        <p className="text-xs text-muted" data-testid="change-list-truncated">
          {t("itsm.change.list.truncated")}
        </p>
      )}

      {!onTasks && items.length > 0 && (
        <div className="flex items-center justify-between gap-3 text-xs text-muted" data-testid="change-list-paging">
          <span data-testid="change-list-range">
            {truncated
              ? t("itsm.change.list.totalAtLeast", { count: total })
              : t("itsm.change.list.range", { from: offset + 1, to: offset + items.length, total })}
          </span>
          <div className="flex gap-2">
            <Button size="sm" data-testid="change-list-prev" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              {t("itsm.change.list.prev")}
            </Button>
            <Button size="sm" data-testid="change-list-next" disabled={offset + items.length >= total} onClick={() => setOffset(offset + PAGE)}>
              {t("itsm.change.list.next")}
            </Button>
          </div>
        </div>
      )}

      {creating && (
        <CreateChangeDialog
          onClose={() => setCreating(false)}
          onCreated={(ticketId) => {
            setCreating(false);
            void navigate({ to: "/agent/itsm/changes/$ticketId", params: { ticketId: String(ticketId) } });
          }}
        />
      )}
    </div>
  );
}

function Filter({ label, htmlFor, children }: { label: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-muted" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
    </div>
  );
}

function ChangeRow({ change: c, serviceName }: { change: ChangeListItem; serviceName: Map<number, string> }) {
  const { t } = useTranslation();
  return (
    <tr data-testid={`change-row-${c.ticket_id}`}>
      <td className="px-3 py-2">
        <RouterAnchor href={changeDetailHref(c.ticket_id)} className="text-accent hover:underline">
          {c.title}
        </RouterAnchor>
        <div className="font-mono text-xs text-muted">{c.tn}</div>
      </td>
      <td className="px-3 py-2">
        <ChangeTypeBadge type={c.change_type} />
      </td>
      <td className="px-3 py-2">
        <Badge tone={PHASE_TONE[c.phase]} data-testid="change-phase-badge" data-phase={c.phase}>
          {t(`itsm.change.phases.${c.phase}`)}
        </Badge>
      </td>
      <td className="px-3 py-2">
        <RiskBadge level={c.risk_level} />
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-xs text-muted">{windowText(c.planned_start, c.planned_end) || t("itsm.change.list.noWindow")}</td>
      <td className="px-3 py-2 text-xs text-muted">{c.service_ids.map((id) => serviceName.get(id) ?? `#${id}`).join(", ")}</td>
    </tr>
  );
}

function TaskTable({ tasks }: { tasks: ChangeTaskListItem[] }) {
  const { t } = useTranslation();
  if (tasks.length === 0) {
    return (
      <p className="rounded-lg border border-hairline bg-surface px-4 py-8 text-center text-sm text-muted" data-testid="change-tasks-empty">
        {t("itsm.change.list.tasksEmpty")}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
      <table className="w-full text-sm" data-testid="change-tasks-table">
        <thead className="border-b border-hairline text-left text-xs text-muted">
          <tr>
            <th className="px-3 py-2 font-medium">{t("itsm.change.list.colTask")}</th>
            <th className="px-3 py-2 font-medium">{t("itsm.change.list.colKind")}</th>
            <th className="px-3 py-2 font-medium">{t("itsm.change.list.colChange")}</th>
            <th className="px-3 py-2 font-medium">{t("itsm.change.list.colState")}</th>
            <th className="px-3 py-2 font-medium">{t("itsm.change.list.colWindow")}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline">
          {tasks.map((k) => (
            <tr key={k.ticket_id} data-testid={`change-task-row-${k.ticket_id}`} data-finished={k.finished}>
              <td className="px-3 py-2">
                <RouterAnchor href={`/agent/tickets/${k.ticket_id}`} className="text-accent hover:underline">
                  {k.title}
                </RouterAnchor>
                <div className="font-mono text-xs text-muted">{k.tn}</div>
              </td>
              <td className="px-3 py-2 text-xs text-muted">{t(`itsm.change.list.kinds.${k.task_kind}`)}</td>
              <td className="px-3 py-2">
                <RouterAnchor href={changeDetailHref(k.change_ticket_id)} className="text-accent hover:underline">
                  {k.change_title ?? k.change_tn ?? `#${k.change_ticket_id}`}
                </RouterAnchor>
              </td>
              <td className="px-3 py-2">
                <Badge tone={k.finished ? "success" : "default"}>{k.state}</Badge>
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-xs text-muted">{windowText(k.planned_start, k.planned_end) || t("itsm.change.list.noWindow")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
