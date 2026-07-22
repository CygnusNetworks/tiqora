import { Fragment, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  api,
  type ErasureMode,
  type ErasureSelectorIn,
  type GdprCustomerRecordPreviewOut,
  type GdprErasureJobDetailOut,
  type GdprErasureJobOut,
  type GdprErasurePreviewOut,
  type GdprResolvedCustomerOut,
} from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { PlusIcon } from "@/components/ui/icons";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { formatDateTime, formatDateOnly } from "@/lib/format";
import { cn } from "@/lib/cn";

const JOBS_KEY = ["admin", "gdpr", "jobs"] as const;
const DELETE_CONFIRM_WORD = "LÖSCHEN";
const LIVE_COUNT_HIGH_THRESHOLD = 1000;

export type GdprSearch = {
  /** Comma-separated customer_user logins prefilled from bulk selection. */
  logins?: string;
  tab?: "run" | "jobs";
};

type ActivityKind = "" | "no_tickets" | "no_open_tickets" | "inactive_since";

type SelectorForm = {
  logins: string[];
  customerIds: string[];
  loginRegex: string;
  loginRegexNegate: boolean;
  customerIdRegex: string;
  customerIdRegexNegate: boolean;
  emailRegex: string;
  emailRegexNegate: boolean;
  changedAfter: string;
  changedBefore: string;
  activityKind: ActivityKind;
  inactiveSince: string;
  validId: "" | "1" | "2" | "3";
  mode: ErasureMode;
  deleteTickets: boolean;
};

const emptyForm = (): SelectorForm => ({
  logins: [],
  customerIds: [],
  loginRegex: "",
  loginRegexNegate: false,
  customerIdRegex: "",
  customerIdRegexNegate: false,
  emailRegex: "",
  emailRegexNegate: false,
  changedAfter: "",
  changedBefore: "",
  activityKind: "",
  inactiveSince: "",
  validId: "",
  mode: "anonymize",
  deleteTickets: false,
});

/** The additional (non-always-visible) selector criteria, managed as a
 * chip-baukasten: each kind is either inactive, being edited inline
 * ("open"), or committed and shown as a removable chip. */
type FilterKind =
  | "loginRegex"
  | "customerIdRegex"
  | "emailRegex"
  | "changedAfter"
  | "changedBefore"
  | "activity"
  | "validId";

/** Pattern-filter kinds that support the "trifft NICHT" negation toggle. */
const NEGATABLE_FILTER_KINDS: FilterKind[] = ["loginRegex", "customerIdRegex", "emailRegex"];

const FILTER_KIND_ORDER: FilterKind[] = [
  "loginRegex",
  "customerIdRegex",
  "emailRegex",
  "changedAfter",
  "changedBefore",
  "activity",
  "validId",
];

function filterKindActive(form: SelectorForm, kind: FilterKind): boolean {
  switch (kind) {
    case "loginRegex":
      return form.loginRegex.trim() !== "";
    case "customerIdRegex":
      return form.customerIdRegex.trim() !== "";
    case "emailRegex":
      return form.emailRegex.trim() !== "";
    case "changedAfter":
      return form.changedAfter !== "";
    case "changedBefore":
      return form.changedBefore !== "";
    case "activity":
      return form.activityKind !== "";
    case "validId":
      return form.validId !== "";
  }
}

function clearFilterKind(form: SelectorForm, kind: FilterKind): SelectorForm {
  switch (kind) {
    case "loginRegex":
      return { ...form, loginRegex: "", loginRegexNegate: false };
    case "customerIdRegex":
      return { ...form, customerIdRegex: "", customerIdRegexNegate: false };
    case "emailRegex":
      return { ...form, emailRegex: "", emailRegexNegate: false };
    case "changedAfter":
      return { ...form, changedAfter: "" };
    case "changedBefore":
      return { ...form, changedBefore: "" };
    case "activity":
      return { ...form, activityKind: "", inactiveSince: "" };
    case "validId":
      return { ...form, validId: "" };
  }
}

/** Negation flag getter/setter for the three negatable pattern filters. */
function filterNegated(form: SelectorForm, kind: FilterKind): boolean {
  switch (kind) {
    case "loginRegex":
      return form.loginRegexNegate;
    case "customerIdRegex":
      return form.customerIdRegexNegate;
    case "emailRegex":
      return form.emailRegexNegate;
    default:
      return false;
  }
}

function setFilterNegated(form: SelectorForm, kind: FilterKind, negate: boolean): SelectorForm {
  switch (kind) {
    case "loginRegex":
      return { ...form, loginRegexNegate: negate };
    case "customerIdRegex":
      return { ...form, customerIdRegexNegate: negate };
    case "emailRegex":
      return { ...form, emailRegexNegate: negate };
    default:
      return form;
  }
}

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

function parsePrefillLogins(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function dateToIsoStart(date: string): string | null {
  if (!date) return null;
  // Date-only input → start of day UTC-ish; backend accepts ISO datetime.
  return new Date(`${date}T00:00:00`).toISOString();
}

function dateToIsoEnd(date: string): string | null {
  if (!date) return null;
  return new Date(`${date}T23:59:59.999`).toISOString();
}

function buildSelector(form: SelectorForm): ErasureSelectorIn {
  let activity: string | null = null;
  if (form.activityKind === "inactive_since" && form.inactiveSince) {
    activity = `inactive_since:${form.inactiveSince}`;
  } else if (form.activityKind === "no_tickets" || form.activityKind === "no_open_tickets") {
    activity = form.activityKind;
  }

  return {
    logins: form.logins,
    customer_ids: form.customerIds,
    login_regex: form.loginRegex.trim() || null,
    login_regex_negate: form.loginRegex.trim() !== "" && form.loginRegexNegate,
    customer_id_regex: form.customerIdRegex.trim() || null,
    customer_id_regex_negate:
      form.customerIdRegex.trim() !== "" && form.customerIdRegexNegate,
    email_regex: form.emailRegex.trim() || null,
    email_regex_negate: form.emailRegex.trim() !== "" && form.emailRegexNegate,
    changed_after: dateToIsoStart(form.changedAfter),
    changed_before: dateToIsoEnd(form.changedBefore),
    activity,
    valid_id: form.validId ? Number(form.validId) : null,
  };
}

function hasAnySelector(selector: ErasureSelectorIn): boolean {
  return Boolean(
    (selector.logins && selector.logins.length > 0) ||
      (selector.customer_ids && selector.customer_ids.length > 0) ||
      selector.login_regex ||
      selector.customer_id_regex ||
      selector.email_regex ||
      selector.changed_after ||
      selector.changed_before ||
      selector.activity ||
      selector.valid_id != null,
  );
}

function statusTone(status: string): "success" | "danger" | "warn" | "muted" | "accent" {
  switch (status) {
    case "applied":
      return "success";
    case "rolled_back":
      return "warn";
    case "purged":
      return "muted";
    default:
      return "accent";
  }
}

function ChipList({
  items,
  onRemove,
  testIdPrefix,
}: {
  items: string[];
  onRemove: (item: string) => void;
  testIdPrefix: string;
}) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-1 flex flex-wrap gap-1" data-testid={`${testIdPrefix}-list`}>
      {items.map((item) => (
        <li
          key={item}
          className="inline-flex items-center gap-1 rounded border border-hairline bg-surface-subtle px-2 py-0.5 font-mono text-xs text-ink"
        >
          <span>{item}</span>
          <button
            type="button"
            className="text-muted hover:text-danger"
            data-testid={`${testIdPrefix}-remove-${item}`}
            aria-label={`Remove ${item}`}
            onClick={() => onRemove(item)}
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  );
}

/** One field row of the per-customer before/after accordion (anonymize mode). */
function RecordFieldRow({
  field,
  before,
  after,
  changed,
  occurrences,
  t,
}: {
  field: string;
  before: unknown;
  after: unknown;
  changed: boolean;
  occurrences: number | null | undefined;
  t: (key: string) => string;
}) {
  const fmt = (v: unknown) => (v == null || v === "" ? "—" : String(v));
  return (
    <tr className="border-b border-hairline last:border-b-0" data-testid={`gdpr-record-field-${field}`}>
      <td className="py-1 pr-3 align-top font-mono text-xs text-muted">
        {field}
        {occurrences != null && <span className="ml-1 text-[10px] text-muted/80">({occurrences})</span>}
      </td>
      <td className="py-1 pr-2 align-top text-xs text-ink">{fmt(before)}</td>
      <td className="py-1 pr-2 align-top text-xs text-muted" aria-hidden>
        →
      </td>
      <td className="py-1 align-top text-xs">
        {changed ? (
          <span className="rounded bg-escalation/15 px-1 py-0.5 text-escalation">{fmt(after)}</span>
        ) : (
          <span className="text-muted">{t("admin.gdpr.recordPreview.unchanged")}</span>
        )}
      </td>
    </tr>
  );
}

/** Inline accordion body: fetches the read-only before/after preview for one
 * customer on first expand and renders it as a small table. */
function RecordPreviewPanel({
  login,
  mode,
  deleteTickets,
}: {
  login: string;
  mode: ErasureMode;
  deleteTickets: boolean;
}) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["admin", "gdpr", "record-preview", login, mode, deleteTickets],
    queryFn: ({ signal }) =>
      api.adminGdpr.recordPreview(
        { login, mode, delete_tickets: mode === "delete" && deleteTickets },
        signal,
      ),
  });

  if (q.isLoading) {
    return (
      <div className="flex items-center gap-2 py-2 text-xs text-muted" data-testid={`gdpr-record-preview-loading-${login}`}>
        <Spinner />
        {t("admin.gdpr.recordPreview.loading")}
      </div>
    );
  }
  if (q.isError) {
    return (
      <p className="py-2 text-xs text-danger" data-testid={`gdpr-record-preview-error-${login}`}>
        {t("admin.gdpr.recordPreview.error")}
      </p>
    );
  }

  const data = q.data as GdprCustomerRecordPreviewOut;
  if (data.mode === "delete") {
    if (data.delete_summary.length === 0) {
      return (
        <p className="py-2 text-xs text-muted" data-testid={`gdpr-record-preview-panel-${login}`}>
          {t("admin.gdpr.recordPreview.deleteEmpty")}
        </p>
      );
    }
    return (
      <table className="w-full text-left text-xs" data-testid={`gdpr-record-preview-panel-${login}`}>
        <thead>
          <tr className="text-[10px] uppercase tracking-wide text-muted">
            <th className="py-1 pr-3 font-medium">{t("admin.gdpr.recordPreview.deleteTable")}</th>
            <th className="py-1 font-medium">{t("admin.gdpr.recordPreview.deleteCount")}</th>
          </tr>
        </thead>
        <tbody>
          {data.delete_summary.map((row) => (
            <tr key={row.table} className="border-b border-hairline last:border-b-0">
              <td className="py-1 pr-3 font-mono text-muted">{row.table}</td>
              <td className="py-1 tabular-nums text-ink">{row.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  if (data.fields.length === 0) {
    return (
      <p className="py-2 text-xs text-muted" data-testid={`gdpr-record-preview-panel-${login}`}>
        {t("admin.gdpr.recordPreview.anonymizeEmpty")}
      </p>
    );
  }
  return (
    <table className="w-full text-left text-xs" data-testid={`gdpr-record-preview-panel-${login}`}>
      <thead>
        <tr className="text-[10px] uppercase tracking-wide text-muted">
          <th className="py-1 pr-3 font-medium">{t("admin.gdpr.recordPreview.field")}</th>
          <th className="py-1 pr-2 font-medium">{t("admin.gdpr.recordPreview.before")}</th>
          <th className="py-1 pr-2 font-medium" aria-hidden />
          <th className="py-1 font-medium">{t("admin.gdpr.recordPreview.after")}</th>
        </tr>
      </thead>
      <tbody>
        {data.fields.map((f) => (
          <RecordFieldRow
            key={f.field}
            field={f.field}
            before={f.before}
            after={f.after}
            changed={f.changed}
            occurrences={f.occurrences}
            t={t}
          />
        ))}
      </tbody>
    </table>
  );
}

/** Customer preview list with a per-row expandable before/after accordion.
 * Kept separate from `DataTable` (which has no row-expansion support) so the
 * "Vorschau" action can open its accordion directly under the clicked row. */
function CustomerPreviewTable({
  columns,
  rows,
  mode,
  deleteTickets,
  t,
}: {
  columns: DataTableColumn<GdprResolvedCustomerOut>[];
  rows: GdprResolvedCustomerOut[];
  mode: ErasureMode;
  deleteTickets: boolean;
  t: (key: string) => string;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div
      className="overflow-x-auto rounded-lg border border-hairline bg-surface"
      data-testid="gdpr-preview-table"
    >
      <table className="w-full min-w-[640px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
            {columns.map((col) => (
              <th key={col.key} className="py-1.5 pl-4 pr-2 font-medium">
                {col.header}
              </th>
            ))}
            <th className="py-1.5 pr-4 text-right font-medium">{t("admin.table.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const isOpen = expanded === row.login;
            return (
              <Fragment key={row.id}>
                <tr
                  data-testid={`admin-row-${row.id}`}
                  className="h-10 border-b border-hairline transition-colors duration-100 hover:bg-surface-subtle last:border-b-0"
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={cn("py-1 pl-4 pr-2 text-xs", col.mono && "font-mono text-muted")}
                    >
                      {col.render(row)}
                    </td>
                  ))}
                  <td className="py-1 pr-4 text-right">
                    <Button
                      size="sm"
                      variant="secondary"
                      data-testid={`gdpr-record-preview-toggle-${row.login}`}
                      onClick={() => setExpanded(isOpen ? null : row.login)}
                    >
                      {isOpen
                        ? t("admin.gdpr.recordPreview.close")
                        : t("admin.gdpr.recordPreview.action")}
                    </Button>
                  </td>
                </tr>
                {isOpen && (
                  <tr className="border-b border-hairline bg-surface-subtle/50 last:border-b-0">
                    <td colSpan={columns.length + 1} className="px-4 py-2">
                      <RecordPreviewPanel login={row.login} mode={mode} deleteTickets={deleteTickets} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function GdprPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const qc = useQueryClient();
  const search = useSearch({ strict: false }) as GdprSearch;

  const [tab, setTab] = useState<"run" | "jobs">(search.tab === "jobs" ? "jobs" : "run");
  const [form, setForm] = useState<SelectorForm>(() => {
    const initial = emptyForm();
    const prefill = parsePrefillLogins(search.logins);
    if (prefill.length) initial.logins = prefill;
    return initial;
  });
  const [openFilterKind, setOpenFilterKind] = useState<FilterKind | null>(null);

  // Apply prefill when navigated with ?logins=… after mount (e.g. bulk action).
  useEffect(() => {
    const prefill = parsePrefillLogins(search.logins);
    if (prefill.length === 0) return;
    setForm((f) => {
      const merged = Array.from(new Set([...f.logins, ...prefill]));
      if (merged.length === f.logins.length) return f;
      return { ...f, logins: merged };
    });
    setTab("run");
  }, [search.logins]);

  const [customerSearch, setCustomerSearch] = useState("");
  const [companyInput, setCompanyInput] = useState("");
  const [loginInput, setLoginInput] = useState("");
  const debouncedCustomerSearch = useDebouncedValue(customerSearch, 300);

  const [preview, setPreview] = useState<GdprErasurePreviewOut | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleteTyped, setDeleteTyped] = useState("");
  const [runResult, setRunResult] = useState<GdprErasureJobDetailOut | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [jobAction, setJobAction] = useState<
    null | { kind: "rollback" | "purge"; job: GdprErasureJobOut }
  >(null);
  const [selectedJob, setSelectedJob] = useState<GdprErasureJobOut | null>(null);
  const [jobsPage, setJobsPage] = useState(1);

  const customersQ = useQuery({
    queryKey: ["admin", "gdpr", "customer-picker", debouncedCustomerSearch],
    queryFn: ({ signal }) =>
      api.searchReferenceCustomers(
        { q: debouncedCustomerSearch.trim() || undefined, limit: 15 },
        signal,
      ),
    enabled: debouncedCustomerSearch.trim().length >= 1,
  });

  const jobsQ = useQuery({
    queryKey: [...JOBS_KEY, jobsPage],
    queryFn: ({ signal }) =>
      api.adminGdpr.listJobs({ page: jobsPage, pageSize: 25 }, signal),
    enabled: tab === "jobs",
  });

  const jobDetailQ = useQuery({
    queryKey: [...JOBS_KEY, "detail", selectedJob?.id],
    queryFn: ({ signal }) => api.adminGdpr.getJob(selectedJob!.id, signal),
    enabled: selectedJob != null,
  });

  const selector = useMemo(() => buildSelector(form), [form]);
  const selectorReady = hasAnySelector(selector);
  const debouncedSelector = useDebouncedValue(selector, 400);
  const debouncedSelectorReady = hasAnySelector(debouncedSelector);

  const liveCountQ = useQuery({
    queryKey: ["admin", "gdpr", "selector-count", debouncedSelector],
    queryFn: ({ signal }) => api.adminGdpr.selectorCount({ selector: debouncedSelector }, signal),
    enabled: debouncedSelectorReady,
  });

  const previewM = useMutation({
    mutationFn: () => {
      return api.adminGdpr.preview({
        selector,
        mode: form.mode,
        delete_tickets: form.mode === "delete" && form.deleteTickets,
      });
    },
    onSuccess: (data) => {
      setPreview(data);
      setPreviewError(null);
      setRunResult(null);
      setRunError(null);
    },
    onError: (err) => {
      setPreview(null);
      setPreviewError(err instanceof Error ? err.message : t("admin.gdpr.previewError"));
    },
  });

  const runM = useMutation({
    mutationFn: () => {
      if (!preview || preview.customers.length === 0) {
        throw new Error(t("admin.gdpr.noCustomers"));
      }
      return api.adminGdpr.createJob({
        customer_user_ids: preview.customers.map((c) => c.id),
        selector,
        mode: form.mode,
        delete_tickets: form.mode === "delete" && form.deleteTickets,
        confirm: true,
      });
    },
    onSuccess: (data) => {
      setConfirmOpen(false);
      setDeleteTyped("");
      setRunResult(data);
      setRunError(null);
      setPreview(null);
      void qc.invalidateQueries({ queryKey: JOBS_KEY });
    },
    onError: (err) => {
      setRunError(err instanceof Error ? err.message : t("admin.gdpr.runError"));
    },
  });

  const rollbackM = useMutation({
    mutationFn: (id: number) => api.adminGdpr.rollback(id),
    onSuccess: async () => {
      setJobAction(null);
      await qc.invalidateQueries({ queryKey: JOBS_KEY });
    },
  });

  const purgeM = useMutation({
    mutationFn: (id: number) => api.adminGdpr.purgeBackup(id),
    onSuccess: async () => {
      setJobAction(null);
      await qc.invalidateQueries({ queryKey: JOBS_KEY });
    },
  });

  const addLogin = (login: string) => {
    const v = login.trim();
    if (!v) return;
    setForm((f) =>
      f.logins.includes(v) ? f : { ...f, logins: [...f.logins, v] },
    );
    setLoginInput("");
    setCustomerSearch("");
  };

  const addCompany = (customerId: string) => {
    const v = customerId.trim();
    if (!v) return;
    setForm((f) =>
      f.customerIds.includes(v) ? f : { ...f, customerIds: [...f.customerIds, v] },
    );
    setCompanyInput("");
  };

  const canRun =
    preview != null &&
    preview.customers.length > 0 &&
    !previewM.isPending &&
    !runM.isPending;

  const deleteConfirmOk =
    form.mode !== "delete" || deleteTyped.trim() === DELETE_CONFIRM_WORD;

  const customerColumns: DataTableColumn<GdprResolvedCustomerOut>[] = useMemo(
    () => [
      {
        key: "login",
        header: t("admin.gdpr.col.login"),
        mono: true,
        render: (r) => r.login,
      },
      {
        key: "email",
        header: t("admin.gdpr.col.email"),
        render: (r) => r.email,
      },
      {
        key: "customer_id",
        header: t("admin.gdpr.col.customerId"),
        mono: true,
        render: (r) => r.customer_id,
      },
      {
        key: "id",
        header: t("admin.table.id"),
        mono: true,
        render: (r) => r.id,
      },
    ],
    [t],
  );

  const jobColumns: DataTableColumn<GdprErasureJobOut>[] = useMemo(
    () => [
      {
        key: "id",
        header: t("admin.table.id"),
        mono: true,
        render: (r) => (
          <button
            type="button"
            className="font-mono text-accent underline-offset-2 hover:underline"
            data-testid={`gdpr-job-id-${r.id}`}
            onClick={() => setSelectedJob(r)}
          >
            {r.id}
          </button>
        ),
      },
      {
        key: "mode",
        header: t("admin.gdpr.col.mode"),
        render: (r) => (
          <Badge tone={r.mode === "delete" ? "danger" : "warn"}>
            {r.mode === "delete"
              ? t("admin.gdpr.modeDelete")
              : t("admin.gdpr.modeAnonymize")}
          </Badge>
        ),
      },
      {
        key: "status",
        header: t("admin.gdpr.col.status"),
        render: (r) => (
          <Badge tone={statusTone(r.status)} data-testid={`gdpr-job-status-${r.id}`}>
            {t(`admin.gdpr.status.${r.status}`, { defaultValue: r.status })}
          </Badge>
        ),
      },
      {
        key: "resolved",
        header: t("admin.gdpr.col.resolved"),
        render: (r) => {
          try {
            const parsed = JSON.parse(r.resolved_logins) as unknown;
            return Array.isArray(parsed) ? parsed.length : "—";
          } catch {
            return r.resolved_logins ? "…" : "—";
          }
        },
      },
      {
        key: "created",
        header: t("admin.gdpr.col.created"),
        render: (r) => formatDateTime(r.created, locale),
      },
      {
        key: "backup_expires",
        header: t("admin.gdpr.col.backupExpires"),
        render: (r) => formatDateTime(r.backup_expires_at, locale),
      },
      {
        key: "actions",
        header: t("admin.gdpr.col.actions"),
        render: (r) => {
          const canRollback = r.status === "applied";
          const canPurge = r.status === "applied" || r.status === "rolled_back";
          const canDownload = r.status !== "purged";
          return (
            <div className="flex flex-wrap gap-1">
              <Button
                size="sm"
                variant="secondary"
                disabled={!canRollback || rollbackM.isPending}
                data-testid={`gdpr-job-rollback-${r.id}`}
                onClick={() => setJobAction({ kind: "rollback", job: r })}
              >
                {t("admin.gdpr.action.rollback")}
              </Button>
              <Button
                size="sm"
                variant="danger"
                disabled={!canPurge || purgeM.isPending}
                data-testid={`gdpr-job-purge-${r.id}`}
                onClick={() => setJobAction({ kind: "purge", job: r })}
              >
                {t("admin.gdpr.action.purge")}
              </Button>
              {canDownload ? (
                <a
                  href={api.adminGdpr.backupDownloadUrl(r.id)}
                  className="inline-flex items-center rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-accent hover:bg-surface-subtle"
                  data-testid={`gdpr-job-download-${r.id}`}
                >
                  {t("admin.gdpr.action.download")}
                </a>
              ) : (
                <Button size="sm" variant="ghost" disabled>
                  {t("admin.gdpr.action.download")}
                </Button>
              )}
            </div>
          );
        },
      },
    ],
    [t, locale, rollbackM.isPending, purgeM.isPending],
  );

  const countEntries = preview
    ? Object.entries(preview.counts).sort(([a], [b]) => a.localeCompare(b))
    : [];

  // ── Chip-baukasten: filter kind labels/values + the summary rail sentences ──
  const filterLabel = (kind: FilterKind): string => {
    switch (kind) {
      case "loginRegex":
        return t("admin.gdpr.field.loginRegex");
      case "customerIdRegex":
        return t("admin.gdpr.field.customerIdRegex");
      case "emailRegex":
        return t("admin.gdpr.field.emailRegex");
      case "changedAfter":
        return t("admin.gdpr.field.changedAfter");
      case "changedBefore":
        return t("admin.gdpr.field.changedBefore");
      case "activity":
        return t("admin.gdpr.field.activity");
      case "validId":
        return t("admin.gdpr.field.validity");
    }
  };

  const activityValueText = (): string => {
    if (form.activityKind === "no_tickets") return t("admin.gdpr.activity.noTickets");
    if (form.activityKind === "no_open_tickets") return t("admin.gdpr.activity.noOpenTickets");
    if (form.activityKind === "inactive_since") {
      return form.inactiveSince
        ? `${t("admin.gdpr.activity.inactiveSince")} ${formatDateOnly(form.inactiveSince, locale)}`
        : t("admin.gdpr.activity.inactiveSince");
    }
    return "";
  };

  const validityValueText = (): string => {
    if (form.validId === "1") return t("admin.table.valid");
    if (form.validId === "2") return t("admin.table.invalid");
    if (form.validId === "3") return t("admin.gdpr.validity.temp");
    return "";
  };

  const filterValueText = (kind: FilterKind): string => {
    switch (kind) {
      case "loginRegex":
        return form.loginRegex;
      case "customerIdRegex":
        return form.customerIdRegex;
      case "emailRegex":
        return form.emailRegex;
      case "changedAfter":
        return formatDateOnly(form.changedAfter, locale);
      case "changedBefore":
        return formatDateOnly(form.changedBefore, locale);
      case "activity":
        return activityValueText();
      case "validId":
        return validityValueText();
    }
  };

  /** Human-readable "…deren Login NICHT mit EDU- beginnt…"-style sentence for
   * a negatable pattern filter; falls back to the plain "label: value" form
   * for non-negatable filters. */
  const filterSummaryText = (kind: FilterKind): string => {
    const value = filterValueText(kind);
    if (!NEGATABLE_FILTER_KINDS.includes(kind)) return `${filterLabel(kind)}: ${value}`;
    return filterNegated(form, kind)
      ? t("admin.gdpr.summary.patternNegated", { label: filterLabel(kind), value })
      : t("admin.gdpr.summary.pattern", { label: filterLabel(kind), value });
  };

  const activeFilterKinds = FILTER_KIND_ORDER.filter(
    (k) => filterKindActive(form, k) && openFilterKind !== k,
  );
  const availableFilterItems: SelectMenuItem<FilterKind>[] = FILTER_KIND_ORDER.filter(
    (k) => !filterKindActive(form, k) && openFilterKind !== k,
  ).map((k) => ({ value: k, label: filterLabel(k) }));

  const summarySentences: string[] = [];
  if (form.logins.length > 0) {
    summarySentences.push(t("admin.gdpr.summary.logins", { count: form.logins.length }));
  }
  if (form.customerIds.length > 0) {
    summarySentences.push(
      t("admin.gdpr.summary.companies", { list: form.customerIds.join(", ") }),
    );
  }
  for (const kind of activeFilterKinds) {
    const value = filterValueText(kind);
    if (!value) continue;
    summarySentences.push(filterSummaryText(kind));
  }

  const liveCount = liveCountQ.data?.count;
  const liveCountTone =
    liveCount === 0 ? "text-danger" : (liveCount ?? 0) > LIVE_COUNT_HIGH_THRESHOLD ? "text-escalation" : "text-ink";

  return (
    <div className="flex flex-col gap-4" data-testid="admin-gdpr-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.gdpr.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("admin.gdpr.subtitle")}</p>
      </div>

      <div className="flex gap-2 border-b border-hairline" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "run"}
          data-testid="gdpr-tab-run"
          className={cn(
            "border-b-2 px-3 py-2 text-sm font-medium",
            tab === "run"
              ? "border-accent text-accent"
              : "border-transparent text-muted hover:text-ink",
          )}
          onClick={() => setTab("run")}
        >
          {t("admin.gdpr.tab.run")}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "jobs"}
          data-testid="gdpr-tab-jobs"
          className={cn(
            "border-b-2 px-3 py-2 text-sm font-medium",
            tab === "jobs"
              ? "border-accent text-accent"
              : "border-transparent text-muted hover:text-ink",
          )}
          onClick={() => setTab("jobs")}
        >
          {t("admin.gdpr.tab.jobs")}
        </button>
      </div>

      {tab === "run" && (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
            {/* LEFT: mode toggle, always-visible customer search, chip-baukasten */}
            <div
              className="rounded-lg border border-hairline bg-surface p-4"
              data-testid="gdpr-selector-form"
            >
              <div className="mb-4 flex flex-wrap items-center gap-3">
                <span className="inline-flex items-center gap-1.5 text-sm font-medium text-ink">
                  {t("admin.gdpr.mode")}
                  <HelpPopover title={t("admin.gdpr.mode")} testId="gdpr-help-mode">
                    {t("admin.help.gdpr.mode")}
                  </HelpPopover>
                </span>
                <div className="inline-flex rounded-md border border-hairline p-0.5">
                  <button
                    type="button"
                    data-testid="gdpr-mode-anonymize"
                    className={cn(
                      "rounded px-3 py-1.5 text-sm",
                      form.mode === "anonymize"
                        ? "bg-escalation/20 font-medium text-escalation"
                        : "text-muted hover:text-ink",
                    )}
                    onClick={() => {
                      setForm((f) => ({ ...f, mode: "anonymize" }));
                      setPreview(null);
                      setRunResult(null);
                    }}
                  >
                    {t("admin.gdpr.modeAnonymize")}
                  </button>
                  <button
                    type="button"
                    data-testid="gdpr-mode-delete"
                    className={cn(
                      "rounded px-3 py-1.5 text-sm",
                      form.mode === "delete"
                        ? "bg-danger/20 font-medium text-danger"
                        : "text-muted hover:text-ink",
                    )}
                    onClick={() => {
                      setForm((f) => ({ ...f, mode: "delete" }));
                      setPreview(null);
                      setRunResult(null);
                    }}
                  >
                    {t("admin.gdpr.modeDelete")}
                  </button>
                </div>
                <p className="w-full text-xs text-muted">
                  {form.mode === "delete"
                    ? t("admin.gdpr.modeDeleteHint")
                    : t("admin.gdpr.modeAnonymizeHint")}
                </p>
                {form.mode === "delete" && (
                  <label
                    className="flex w-full items-start gap-2 rounded-lg border border-danger/40 bg-danger/5 p-2.5 text-xs text-ink"
                    data-testid="gdpr-delete-tickets"
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={form.deleteTickets}
                      onChange={(e) => {
                        setForm((f) => ({ ...f, deleteTickets: e.target.checked }));
                        setPreview(null);
                      }}
                    />
                    <span>{t("admin.gdpr.deleteTicketsHint")}</span>
                  </label>
                )}
              </div>

              {/* Always-visible customer search */}
              <div>
                <label className="block text-xs text-muted">
                  {t("admin.gdpr.field.customerSearch")}
                  <input
                    type="search"
                    data-testid="gdpr-customer-search"
                    value={customerSearch}
                    onChange={(e) => setCustomerSearch(e.target.value)}
                    placeholder={t("admin.gdpr.field.customerSearchPh")}
                    className="mt-1 w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
                  />
                </label>
                {customersQ.data && customersQ.data.length > 0 && (
                  <ul
                    className="mt-1 max-h-40 overflow-y-auto rounded-md border border-hairline"
                    data-testid="gdpr-customer-results"
                  >
                    {customersQ.data.map((c) => (
                      <li key={c.login}>
                        <button
                          type="button"
                          className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left text-xs hover:bg-surface-subtle"
                          data-testid={`gdpr-customer-option-${c.login}`}
                          onClick={() => addLogin(c.login)}
                        >
                          <span className="font-mono text-accent">{c.login}</span>
                          <span className="truncate text-muted">
                            {c.full_name || c.email} · {c.customer_id}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="mt-2 flex gap-2">
                  <input
                    type="text"
                    data-testid="gdpr-login-input"
                    value={loginInput}
                    onChange={(e) => setLoginInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addLogin(loginInput);
                      }
                    }}
                    placeholder={t("admin.gdpr.field.loginManualPh")}
                    className="min-w-0 flex-1 rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
                  />
                  <Button
                    size="sm"
                    variant="secondary"
                    data-testid="gdpr-login-add"
                    onClick={() => addLogin(loginInput)}
                  >
                    {t("admin.gdpr.add")}
                  </Button>
                </div>
                <ChipList
                  items={form.logins}
                  testIdPrefix="gdpr-login"
                  onRemove={(login) =>
                    setForm((f) => ({
                      ...f,
                      logins: f.logins.filter((x) => x !== login),
                    }))
                  }
                />
              </div>

              <div className="mt-4">
                <label className="block text-xs text-muted">
                  {t("admin.gdpr.field.companyId")}
                  <div className="mt-1 flex gap-2">
                    <input
                      type="text"
                      data-testid="gdpr-company-input"
                      value={companyInput}
                      onChange={(e) => setCompanyInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addCompany(companyInput);
                        }
                      }}
                      placeholder={t("admin.gdpr.field.companyIdPh")}
                      className="min-w-0 flex-1 rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 font-mono text-sm text-ink"
                    />
                    <Button
                      size="sm"
                      variant="secondary"
                      data-testid="gdpr-company-add"
                      onClick={() => addCompany(companyInput)}
                    >
                      {t("admin.gdpr.add")}
                    </Button>
                  </div>
                </label>
                <ChipList
                  items={form.customerIds}
                  testIdPrefix="gdpr-company"
                  onRemove={(id) =>
                    setForm((f) => ({
                      ...f,
                      customerIds: f.customerIds.filter((x) => x !== id),
                    }))
                  }
                />
              </div>

              {/* Chip-baukasten: additional selectors */}
              <div className="mt-4 border-t border-hairline pt-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 text-xs font-medium text-ink">
                    {t("admin.gdpr.filters.title")}
                    <HelpPopover title={t("admin.gdpr.filters.title")} testId="gdpr-help-filters">
                      {t("admin.help.gdpr.filters")}
                    </HelpPopover>
                  </span>
                  <SelectMenu
                    items={availableFilterItems}
                    onSelect={(kind) => setOpenFilterKind(kind)}
                    placeholder={t("admin.gdpr.filters.addPlaceholder")}
                    panelTestId="gdpr-add-filter-panel"
                    trigger={({ ref, toggleProps }) => (
                      <button
                        ref={ref}
                        type="button"
                        data-testid="gdpr-add-filter"
                        disabled={availableFilterItems.length === 0}
                        {...toggleProps}
                        className="inline-flex items-center gap-1 rounded-full border border-hairline bg-surface-subtle px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <PlusIcon className="text-[12px]" />
                        {t("admin.gdpr.filters.addButton")}
                      </button>
                    )}
                  />
                </div>

                <div className="flex flex-wrap gap-1.5" data-testid="gdpr-filter-chips">
                  {activeFilterKinds.length === 0 && !openFilterKind && (
                    <p className="text-xs text-muted">{t("admin.gdpr.filters.noneActive")}</p>
                  )}
                  {activeFilterKinds.map((kind) => (
                    <span
                      key={kind}
                      data-testid={`gdpr-filter-chip-${kind}`}
                      className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent-dim px-2.5 py-1 text-xs text-accent"
                    >
                      <span>
                        {filterLabel(kind)}
                        {NEGATABLE_FILTER_KINDS.includes(kind) && filterNegated(form, kind)
                          ? " ≠ "
                          : ": "}
                        {filterValueText(kind)}
                      </span>
                      <button
                        type="button"
                        data-testid={`gdpr-filter-chip-remove-${kind}`}
                        aria-label={t("admin.gdpr.filters.remove", { label: filterLabel(kind) })}
                        className="text-accent/70 hover:text-danger"
                        onClick={() => setForm((f) => clearFilterKind(f, kind))}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>

                {openFilterKind && (
                  <div
                    className="mt-2 rounded-lg border border-hairline bg-surface-subtle p-2.5"
                    data-testid={`gdpr-filter-open-${openFilterKind}`}
                  >
                    <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-ink">
                      {filterLabel(openFilterKind)}
                      {NEGATABLE_FILTER_KINDS.includes(openFilterKind) && (
                        <HelpPopover
                          title={filterLabel(openFilterKind)}
                          testId={`gdpr-help-negate-${openFilterKind}`}
                        >
                          {t("admin.help.gdpr.negate")}
                        </HelpPopover>
                      )}
                      {openFilterKind === "validId" && (
                        <HelpPopover
                          title={filterLabel(openFilterKind)}
                          testId="gdpr-help-valid-id"
                        >
                          {t("admin.help.gdpr.validId")}
                        </HelpPopover>
                      )}
                    </p>
                    {NEGATABLE_FILTER_KINDS.includes(openFilterKind) && (
                      <div
                        className="mb-1.5 inline-flex rounded-md border border-hairline p-0.5"
                        role="group"
                        aria-label={t("admin.gdpr.filters.negateToggle")}
                        data-testid={`gdpr-filter-negate-${openFilterKind}`}
                      >
                        <button
                          type="button"
                          data-testid={`gdpr-filter-negate-off-${openFilterKind}`}
                          className={cn(
                            "rounded px-2 py-1 text-xs font-mono",
                            !filterNegated(form, openFilterKind)
                              ? "bg-accent-dim font-medium text-accent"
                              : "text-muted hover:text-ink",
                          )}
                          onClick={() =>
                            setForm((f) => setFilterNegated(f, openFilterKind, false))
                          }
                        >
                          = {t("admin.gdpr.filters.matches")}
                        </button>
                        <button
                          type="button"
                          data-testid={`gdpr-filter-negate-on-${openFilterKind}`}
                          className={cn(
                            "rounded px-2 py-1 text-xs font-mono",
                            filterNegated(form, openFilterKind)
                              ? "bg-danger/20 font-medium text-danger"
                              : "text-muted hover:text-ink",
                          )}
                          onClick={() =>
                            setForm((f) => setFilterNegated(f, openFilterKind, true))
                          }
                        >
                          ≠ {t("admin.gdpr.filters.matchesNot")}
                        </button>
                      </div>
                    )}
                    {openFilterKind === "loginRegex" && (
                      <input
                        type="text"
                        autoFocus
                        value={form.loginRegex}
                        onChange={(e) => setForm((f) => ({ ...f, loginRegex: e.target.value }))}
                        placeholder="^old-user-.*"
                        className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 font-mono text-sm text-ink"
                      />
                    )}
                    {openFilterKind === "customerIdRegex" && (
                      <input
                        type="text"
                        autoFocus
                        value={form.customerIdRegex}
                        onChange={(e) =>
                          setForm((f) => ({ ...f, customerIdRegex: e.target.value }))
                        }
                        placeholder="^LEGACY-.*"
                        className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 font-mono text-sm text-ink"
                      />
                    )}
                    {openFilterKind === "emailRegex" && (
                      <input
                        type="text"
                        autoFocus
                        data-testid="gdpr-email-regex-input"
                        value={form.emailRegex}
                        onChange={(e) => setForm((f) => ({ ...f, emailRegex: e.target.value }))}
                        placeholder="@edu\.example$"
                        className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 font-mono text-sm text-ink"
                      />
                    )}
                    {openFilterKind === "changedAfter" && (
                      <input
                        type="date"
                        autoFocus
                        value={form.changedAfter}
                        onChange={(e) => setForm((f) => ({ ...f, changedAfter: e.target.value }))}
                        className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
                      />
                    )}
                    {openFilterKind === "changedBefore" && (
                      <input
                        type="date"
                        autoFocus
                        value={form.changedBefore}
                        onChange={(e) => setForm((f) => ({ ...f, changedBefore: e.target.value }))}
                        className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
                      />
                    )}
                    {openFilterKind === "activity" && (
                      <div className="space-y-2">
                        <select
                          data-testid="gdpr-activity"
                          value={form.activityKind}
                          onChange={(e) =>
                            setForm((f) => ({
                              ...f,
                              activityKind: e.target.value as ActivityKind,
                            }))
                          }
                          className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
                        >
                          <option value="">{t("admin.gdpr.activity.all")}</option>
                          <option value="no_tickets">{t("admin.gdpr.activity.noTickets")}</option>
                          <option value="no_open_tickets">
                            {t("admin.gdpr.activity.noOpenTickets")}
                          </option>
                          <option value="inactive_since">
                            {t("admin.gdpr.activity.inactiveSince")}
                          </option>
                        </select>
                        {form.activityKind === "inactive_since" && (
                          <input
                            type="date"
                            data-testid="gdpr-inactive-since"
                            value={form.inactiveSince}
                            onChange={(e) =>
                              setForm((f) => ({ ...f, inactiveSince: e.target.value }))
                            }
                            className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
                          />
                        )}
                      </div>
                    )}
                    {openFilterKind === "validId" && (
                      <select
                        data-testid="gdpr-valid-id"
                        value={form.validId}
                        onChange={(e) =>
                          setForm((f) => ({
                            ...f,
                            validId: e.target.value as SelectorForm["validId"],
                          }))
                        }
                        className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
                      >
                        <option value="">{t("admin.gdpr.validity.all")}</option>
                        <option value="1">{t("admin.table.valid")}</option>
                        <option value="2">{t("admin.table.invalid")}</option>
                        <option value="3">{t("admin.gdpr.validity.temp")}</option>
                      </select>
                    )}
                    <div className="mt-2 flex justify-end gap-2">
                      <Button
                        size="sm"
                        variant="secondary"
                        data-testid={`gdpr-filter-cancel-${openFilterKind}`}
                        onClick={() => {
                          setForm((f) => clearFilterKind(f, openFilterKind));
                          setOpenFilterKind(null);
                        }}
                      >
                        {t("admin.form.cancel")}
                      </Button>
                      <Button
                        size="sm"
                        variant="primary"
                        data-testid={`gdpr-filter-commit-${openFilterKind}`}
                        disabled={!filterKindActive(form, openFilterKind)}
                        onClick={() => setOpenFilterKind(null)}
                      >
                        {t("admin.gdpr.filters.commit")}
                      </Button>
                    </div>
                  </div>
                )}
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  data-testid="gdpr-preview"
                  disabled={!selectorReady || previewM.isPending}
                  onClick={() => {
                    setPreviewError(null);
                    previewM.mutate();
                  }}
                >
                  {previewM.isPending ? <Spinner /> : t("admin.gdpr.preview")}
                </Button>
                <Button
                  variant="secondary"
                  data-testid="gdpr-reset"
                  onClick={() => {
                    setForm(emptyForm());
                    setOpenFilterKind(null);
                    setPreview(null);
                    setPreviewError(null);
                    setRunResult(null);
                    setRunError(null);
                  }}
                >
                  {t("admin.gdpr.reset")}
                </Button>
              </div>
              {previewError && (
                <p className="mt-2 text-sm text-danger" data-testid="gdpr-preview-error">
                  {previewError}
                </p>
              )}
            </div>

            {/* RIGHT: live summary rail */}
            <div
              className="h-fit rounded-lg border border-hairline bg-surface p-4"
              data-testid="gdpr-summary-rail"
            >
              <h2 className="mb-2 font-display text-sm font-semibold text-ink">
                {t("admin.gdpr.summary.title")}
              </h2>
              <ul className="space-y-1 text-xs text-ink" data-testid="gdpr-summary-sentences">
                {summarySentences.length === 0 ? (
                  <li className="text-muted">{t("admin.gdpr.summary.empty")}</li>
                ) : (
                  summarySentences.map((s, i) => <li key={i}>{s}</li>)
                )}
              </ul>

              <div className="mt-3 border-t border-hairline pt-3">
                <p className="text-[10px] font-medium uppercase tracking-wide text-muted">
                  {t("admin.gdpr.liveCount.label")}
                </p>
                {!debouncedSelectorReady ? (
                  <p className="mt-1 text-2xl font-semibold tabular-nums text-muted" data-testid="gdpr-live-count">
                    —
                  </p>
                ) : liveCountQ.isLoading ? (
                  <p className="mt-1 text-sm text-muted" data-testid="gdpr-live-count">
                    {t("admin.gdpr.liveCount.loading")}
                  </p>
                ) : liveCountQ.isError ? (
                  <p className="mt-1 text-sm text-danger" data-testid="gdpr-live-count">
                    {t("admin.gdpr.liveCount.error")}
                  </p>
                ) : (
                  <p
                    className={cn("mt-1 text-2xl font-semibold tabular-nums", liveCountTone)}
                    data-testid="gdpr-live-count"
                  >
                    {liveCount}
                  </p>
                )}
                {liveCount === 0 && (
                  <p className="mt-1 text-xs text-danger" data-testid="gdpr-live-count-hint">
                    {t("admin.gdpr.liveCount.zero")}
                  </p>
                )}
                {(liveCount ?? 0) > LIVE_COUNT_HIGH_THRESHOLD && (
                  <p className="mt-1 text-xs text-escalation" data-testid="gdpr-live-count-hint">
                    {t("admin.gdpr.liveCount.high")}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Preview result */}
          {preview && (
            <div
              className="space-y-3 rounded-lg border border-hairline bg-surface p-4"
              data-testid="gdpr-preview-panel"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h2 className="font-display text-base font-semibold text-ink">
                    {t("admin.gdpr.previewTitle")}
                  </h2>
                  <p className="mt-0.5 text-xs text-muted">
                    {t("admin.gdpr.previewSummary", {
                      count: preview.customers.length,
                      mode:
                        preview.mode === "delete"
                          ? t("admin.gdpr.modeDelete")
                          : t("admin.gdpr.modeAnonymize"),
                    })}
                  </p>
                </div>
                <Button
                  variant={form.mode === "delete" ? "danger" : "primary"}
                  data-testid="gdpr-run"
                  disabled={!canRun}
                  onClick={() => {
                    setDeleteTyped("");
                    setRunError(null);
                    setConfirmOpen(true);
                  }}
                >
                  {t("admin.gdpr.run")}
                </Button>
              </div>

              {preview.customers.length === 0 ? (
                <p className="text-sm text-muted" data-testid="gdpr-preview-empty">
                  {t("admin.gdpr.previewEmpty")}
                </p>
              ) : (
                <>
                  <CustomerPreviewTable
                    columns={customerColumns}
                    rows={preview.customers}
                    mode={form.mode}
                    deleteTickets={form.deleteTickets}
                    t={t}
                  />

                  <div data-testid="gdpr-preview-counts">
                    <h3 className="mb-2 text-sm font-medium text-ink">
                      {t("admin.gdpr.affectedCounts")}
                    </h3>
                    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
                      {countEntries.map(([key, value]) => (
                        <div
                          key={key}
                          className="rounded border border-hairline bg-surface-subtle px-2 py-1.5"
                          data-testid={`gdpr-count-${key}`}
                        >
                          <dt className="truncate font-mono text-[10px] uppercase tracking-wide text-muted">
                            {key}
                          </dt>
                          <dd className="font-mono text-sm tabular-nums text-ink">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>

                  <div
                    className={cn(
                      "rounded border px-3 py-2 text-xs",
                      form.mode === "delete"
                        ? "border-danger/40 bg-danger/10 text-danger"
                        : "border-escalation/40 bg-escalation/10 text-escalation",
                    )}
                    data-testid="gdpr-mode-impact"
                  >
                    {form.mode === "delete" ? (
                      <>
                        <p className="font-medium">{t("admin.gdpr.impact.deleteTitle")}</p>
                        <p className="mt-1">
                          {t("admin.gdpr.impact.deleteBody", {
                            tables: preview.tables_deleted.join(", ") || "—",
                          })}
                        </p>
                      </>
                    ) : (
                      <>
                        <p className="font-medium">{t("admin.gdpr.impact.anonymizeTitle")}</p>
                        <ul className="mt-1 list-inside list-disc">
                          {Object.entries(preview.columns_changed).map(([table, cols]) => (
                            <li key={table}>
                              <span className="font-mono">{table}</span>: {cols.join(", ")}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
          )}

          {runResult && (
            <div
              className="rounded-lg border border-green/40 bg-green/10 p-4 text-sm"
              data-testid="gdpr-run-result"
            >
              <p className="font-medium text-ink">{t("admin.gdpr.runSuccess")}</p>
              <p className="mt-1 text-muted">
                {t("admin.gdpr.runSuccessDetail", {
                  id: runResult.id,
                  status: runResult.status,
                  count: runResult.resolved_logins_parsed?.length ?? "—",
                })}
              </p>
              <Button
                size="sm"
                variant="secondary"
                className="mt-2"
                data-testid="gdpr-goto-jobs"
                onClick={() => setTab("jobs")}
              >
                {t("admin.gdpr.viewJobs")}
              </Button>
            </div>
          )}
          {runError && (
            <p className="text-sm text-danger" data-testid="gdpr-run-error">
              {runError}
            </p>
          )}
        </div>
      )}

      {tab === "jobs" && (
        <div className="space-y-3" data-testid="gdpr-jobs-panel">
          {jobsQ.isLoading ? (
            <div className="flex justify-center p-8">
              <Spinner />
            </div>
          ) : (
            <DataTable
              columns={jobColumns}
              rows={jobsQ.data?.items ?? []}
              rowKey={(r) => r.id}
              testId="gdpr-jobs-table"
              emptyLabel={t("admin.gdpr.jobsEmpty")}
            />
          )}
          {jobsQ.data && jobsQ.data.total > (jobsQ.data.page_size ?? 25) && (
            <div className="flex items-center justify-end gap-2 text-sm">
              <Button
                size="sm"
                variant="secondary"
                disabled={jobsPage <= 1}
                onClick={() => setJobsPage((p) => Math.max(1, p - 1))}
              >
                ←
              </Button>
              <span className="text-muted">
                {jobsPage} / {Math.max(1, Math.ceil(jobsQ.data.total / jobsQ.data.page_size))}
              </span>
              <Button
                size="sm"
                variant="secondary"
                disabled={jobsPage * jobsQ.data.page_size >= jobsQ.data.total}
                onClick={() => setJobsPage((p) => p + 1)}
              >
                →
              </Button>
            </div>
          )}

          {(jobDetailQ.data ?? selectedJob) && (
            <div
              className="rounded-lg border border-hairline bg-surface p-4 text-sm"
              data-testid="gdpr-job-detail"
            >
              <div className="mb-2 flex items-center justify-between">
                <h3 className="font-display font-semibold text-ink">
                  {t("admin.gdpr.jobDetail", {
                    id: (jobDetailQ.data ?? selectedJob)!.id,
                  })}
                </h3>
                <Button size="sm" variant="ghost" onClick={() => setSelectedJob(null)}>
                  ✕
                </Button>
              </div>
              <pre
                className="max-h-64 overflow-auto rounded bg-surface-subtle p-2 font-mono text-xs text-ink"
                data-testid="gdpr-job-selector"
              >
                {jobDetailQ.data
                  ? JSON.stringify(jobDetailQ.data.selector_parsed, null, 2)
                  : selectedJob?.selector}
              </pre>
              {jobDetailQ.data?.counts_parsed && (
                <dl className="mt-2 grid grid-cols-2 gap-1 sm:grid-cols-4">
                  {Object.entries(jobDetailQ.data.counts_parsed).map(([k, v]) => (
                    <div key={k}>
                      <dt className="font-mono text-[10px] text-muted">{k}</dt>
                      <dd className="font-mono text-sm">{v}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          )}
        </div>
      )}

      {/* Confirm run dialog */}
      <Dialog
        open={confirmOpen}
        onClose={() => {
          if (!runM.isPending) {
            setConfirmOpen(false);
            setDeleteTyped("");
          }
        }}
        title={t("admin.gdpr.confirmTitle")}
        className="max-w-lg"
      >
        <div className="space-y-3" data-testid="gdpr-confirm-dialog">
          <p className="text-sm text-ink">
            {form.mode === "delete"
              ? t("admin.gdpr.confirmDeleteBody", { count: preview?.customers.length ?? 0 })
              : t("admin.gdpr.confirmAnonymizeBody", {
                  count: preview?.customers.length ?? 0,
                })}
          </p>
          {form.mode === "delete" && (
            <label className="block text-xs text-muted">
              {t("admin.gdpr.confirmTypePrompt", { word: DELETE_CONFIRM_WORD })}
              <input
                type="text"
                data-testid="gdpr-confirm-type"
                value={deleteTyped}
                onChange={(e) => setDeleteTyped(e.target.value)}
                autoComplete="off"
                className="mt-1 w-full rounded-md border border-danger/40 bg-surface px-3 py-1.5 font-mono text-sm text-ink"
              />
            </label>
          )}
          {runError && (
            <p className="text-xs text-danger" data-testid="gdpr-confirm-error">
              {runError}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={runM.isPending}
              onClick={() => {
                setConfirmOpen(false);
                setDeleteTyped("");
              }}
            >
              {t("admin.form.cancel")}
            </Button>
            <Button
              size="sm"
              variant={form.mode === "delete" ? "danger" : "primary"}
              data-testid="gdpr-confirm-submit"
              disabled={!deleteConfirmOk || runM.isPending}
              onClick={() => runM.mutate()}
            >
              {runM.isPending ? <Spinner /> : t("admin.gdpr.confirmSubmit")}
            </Button>
          </div>
        </div>
      </Dialog>

      {/* Job rollback / purge dialog */}
      <Dialog
        open={jobAction != null}
        onClose={() => {
          if (!rollbackM.isPending && !purgeM.isPending) setJobAction(null);
        }}
        title={
          jobAction?.kind === "purge"
            ? t("admin.gdpr.purgeTitle")
            : t("admin.gdpr.rollbackTitle")
        }
      >
        <div className="space-y-3" data-testid="gdpr-job-action-dialog">
          <p className="text-sm text-ink">
            {jobAction?.kind === "purge"
              ? t("admin.gdpr.purgeBody", { id: jobAction.job.id })
              : t("admin.gdpr.rollbackBody", { id: jobAction?.job.id ?? "" })}
          </p>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={rollbackM.isPending || purgeM.isPending}
              onClick={() => setJobAction(null)}
            >
              {t("admin.form.cancel")}
            </Button>
            <Button
              size="sm"
              variant={jobAction?.kind === "purge" ? "danger" : "primary"}
              data-testid="gdpr-job-action-confirm"
              disabled={rollbackM.isPending || purgeM.isPending}
              onClick={() => {
                if (!jobAction) return;
                if (jobAction.kind === "purge") purgeM.mutate(jobAction.job.id);
                else rollbackM.mutate(jobAction.job.id);
              }}
            >
              {jobAction?.kind === "purge"
                ? t("admin.gdpr.action.purge")
                : t("admin.gdpr.action.rollback")}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
