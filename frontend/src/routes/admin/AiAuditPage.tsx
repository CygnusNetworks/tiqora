import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  aiApi,
  type AiAuditLogDetailOut,
  type AiAuditLogFilterParams,
  type AiAuditLogListItemOut,
  type AuditFeature,
  type AuditRequestStatus,
} from "@/lib/aiApi";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { Tabs } from "@/components/ui/Tabs";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { ChevronDownIcon } from "@/components/ui/icons";
import { ToolResultBody } from "@/components/ai/ToolResultView";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

const QUERY_KEY = ["admin", "ai", "audit"] as const;
const PAGE_SIZE = 25;
const LONG_TEXT_THRESHOLD = 2000;
const PII_TOKEN_RE = /\[(?:EMAIL|NAME|IP|PHONE|IBAN|[A-Z]+)_\d+\]/g;

type PeriodPreset = "today" | "7d" | "30d" | "custom";

type WireMessage = {
  role: string;
  content?:
    | string
    | Array<{ type: string; text?: string; image_url?: { url?: string } }>
    | null;
  tool_calls?: Array<{
    id?: string;
    name?: string;
    arguments?: unknown;
    function?: { name?: string; arguments?: string };
  }>;
  tool_call_id?: string;
  name?: string;
};

type ParsedRequest = {
  messages?: WireMessage[];
  max_tokens?: number;
  temperature?: number;
  tools?: unknown[];
};

type ParsedResponse = {
  content?: string | null;
  tool_calls?: Array<{ id?: string; name?: string; arguments?: unknown }>;
  finish_reason?: string | null;
  model?: string | null;
};

function safeParse<T>(raw: string | null | undefined): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function startOfTodayIso(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

function daysAgoIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString();
}

function statusTone(row: {
  status_code: number | null;
  error: string | null;
}): "success" | "danger" {
  return row.error != null ||
    (row.status_code != null && row.status_code >= 400)
    ? "danger"
    : "success";
}

function featureTone(feature: AuditFeature): "accent" | "muted" {
  return feature === "test" ? "muted" : "accent";
}

function tokensLabel(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/** Cost display: real currency formatting for 3-letter ISO codes, plain
 * number + suffix otherwise. Sub-cent amounts keep 4 fraction digits so
 * small per-request costs don't collapse to 0,00. */
function formatCost(
  value: number,
  currency: string | null,
  locale: string,
): string {
  const digits = value !== 0 && Math.abs(value) < 0.01 ? 4 : 2;
  if (currency && /^[A-Z]{3}$/.test(currency)) {
    try {
      return new Intl.NumberFormat(locale, {
        style: "currency",
        currency,
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(value);
    } catch {
      // fall through to plain formatting for unknown codes
    }
  }
  const num = new Intl.NumberFormat(locale, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
  return currency ? `${num} ${currency}` : num;
}

function StatCard({
  label,
  value,
  hint,
  tone,
  testId,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "danger";
  testId: string;
}) {
  return (
    <div
      className="rounded-lg border border-hairline bg-surface p-4"
      data-testid={testId}
    >
      <p className="truncate text-xs uppercase tracking-wide text-muted">
        {label}
      </p>
      <p
        className={cn(
          "mt-2 font-mono text-2xl font-semibold tabular-nums",
          tone === "danger" ? "text-danger" : "text-ink",
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-1 truncate text-xs text-muted">{hint}</p>}
    </div>
  );
}

function PerDayChart({
  perDay,
}: {
  perDay: { date: string; count: number }[];
}) {
  const { i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  if (perDay.length === 0) return null;
  const max = Math.max(1, ...perDay.map((d) => d.count));
  return (
    <div
      className="flex h-24 items-end gap-1 rounded-lg border border-hairline bg-surface p-3"
      data-testid="ai-audit-chart"
    >
      {perDay.map((d) => (
        <div
          key={d.date}
          className="flex flex-1 flex-col items-center justify-end gap-1"
          title={`${d.date}: ${d.count}`}
        >
          <div
            className="w-full rounded-t bg-accent/60"
            style={{ height: `${Math.max(4, (d.count / max) * 100)}%` }}
          />
          <span className="truncate text-[9px] text-muted">
            {new Date(d.date).toLocaleDateString(locale, {
              day: "2-digit",
              month: "2-digit",
            })}
          </span>
        </div>
      ))}
    </div>
  );
}

function renderHighlightedText(
  text: string,
  piiMap: Record<string, string> | null,
): ReactNode[] {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  const re = new RegExp(PII_TOKEN_RE.source, "g");
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
    const token = match[0];
    const revealed = piiMap?.[token];
    parts.push(
      <span
        key={`pii-${i++}-${match.index}`}
        className={cn(
          "rounded bg-fuchsia-300/15 px-0.5 text-fuchsia-600 dark:text-fuchsia-300",
          revealed != null &&
            "underline decoration-dashed decoration-fuchsia-500",
        )}
        title={revealed != null ? token : undefined}
      >
        {revealed ?? token}
      </span>,
    );
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function CollapsibleText({
  text,
  piiMap,
}: {
  text: string;
  piiMap: Record<string, string> | null;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > LONG_TEXT_THRESHOLD;
  const shown =
    isLong && !expanded ? `${text.slice(0, LONG_TEXT_THRESHOLD)}…` : text;
  return (
    <div className="whitespace-pre-wrap break-words text-sm text-ink">
      {renderHighlightedText(shown, piiMap)}
      {isLong && (
        <button
          type="button"
          className="ml-1 text-xs font-medium text-accent hover:underline"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded
            ? t("admin.ai.audit.detail.collapse")
            : t("admin.ai.audit.detail.showFull")}
        </button>
      )}
    </div>
  );
}

const ROLE_STYLES: Record<string, string> = {
  system: "border-hairline bg-surface-subtle",
  user: "border-green/35 bg-green/10",
  assistant: "border-accent/35 bg-accent/10",
  tool: "border-amber/35 bg-amber/10",
};

function MessageBlock({
  role,
  label,
  children,
}: {
  role: string;
  label?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border p-3",
        ROLE_STYLES[role] ?? ROLE_STYLES.system,
      )}
    >
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
        {label ?? role}
      </div>
      {children}
    </div>
  );
}

function formatToolCalls(toolCalls: WireMessage["tool_calls"]): string {
  if (!toolCalls || toolCalls.length === 0) return "";
  return toolCalls
    .map((tc) => {
      const name = tc.name ?? tc.function?.name ?? "?";
      let args: unknown = tc.arguments;
      if (tc.function?.arguments != null) {
        try {
          args = JSON.parse(tc.function.arguments);
        } catch {
          args = tc.function.arguments;
        }
      }
      return `${name}(${JSON.stringify(args)})`;
    })
    .join("\n");
}

function MessagesTab({
  parsedRequest,
  parsedResponse,
  entry,
  piiMap,
}: {
  parsedRequest: ParsedRequest | null;
  parsedResponse: ParsedResponse | null;
  entry: AiAuditLogDetailOut;
  piiMap: Record<string, string> | null;
}) {
  const { t } = useTranslation();
  const messages = parsedRequest?.messages ?? [];

  return (
    <div className="space-y-2" data-testid="ai-audit-messages-tab">
      {messages.map((m, idx) => {
        const contentText =
          typeof m.content === "string"
            ? m.content
            : Array.isArray(m.content)
              ? m.content
                  .map((part) =>
                    part.type === "text"
                      ? (part.text ?? "")
                      : part.type === "image_url"
                        ? `[${part.image_url?.url ?? "image"}]`
                        : "",
                  )
                  .join("\n")
              : "";
        const toolCallsText = formatToolCalls(m.tool_calls);
        return (
          <MessageBlock
            key={`${m.role}-${idx}`}
            role={m.role}
            label={m.name ? `${m.role} (${m.name})` : undefined}
          >
            {contentText &&
              (m.role === "tool" ? (
                // Formatted tool result (key/value grid / pretty JSON) — the
                // PII token hover-reveal is traded for readability here;
                // tokens still show literally inside the values.
                <ToolResultBody content={contentText} />
              ) : (
                <CollapsibleText text={contentText} piiMap={piiMap} />
              ))}
            {toolCallsText && (
              <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-amber">
                {toolCallsText}
              </pre>
            )}
            {!contentText && !toolCallsText && (
              <span className="text-xs text-muted">
                {t("admin.ai.audit.detail.empty")}
              </span>
            )}
          </MessageBlock>
        );
      })}

      {parsedResponse && (
        <MessageBlock
          role="assistant"
          label={t("admin.ai.audit.detail.response")}
        >
          {parsedResponse.content && (
            <CollapsibleText text={parsedResponse.content} piiMap={piiMap} />
          )}
          {parsedResponse.tool_calls &&
            parsedResponse.tool_calls.length > 0 && (
              <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-amber">
                {formatToolCalls(parsedResponse.tool_calls)}
              </pre>
            )}
        </MessageBlock>
      )}

      {entry.error && (
        <MessageBlock role="error" label={t("admin.ai.audit.detail.error")}>
          <p className="whitespace-pre-wrap break-words text-sm text-danger">
            {entry.error}
          </p>
        </MessageBlock>
      )}
    </div>
  );
}

function downloadJson(filename: string, content: string) {
  const blob = new Blob([content], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function RawJsonBox({
  label,
  raw,
  testId,
}: {
  label: string;
  raw: string | null;
  testId: string;
}) {
  const { t } = useTranslation();
  const pretty = useMemo(() => {
    if (!raw) return null;
    const parsed = safeParse<unknown>(raw);
    return parsed != null ? JSON.stringify(parsed, null, 2) : raw;
  }, [raw]);

  if (pretty == null) {
    return (
      <p className="text-xs text-muted">{t("admin.ai.audit.detail.empty")}</p>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {label}
        </span>
        <div className="flex gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void navigator.clipboard.writeText(pretty)}
            data-testid={`${testId}-copy`}
          >
            {t("admin.ai.audit.detail.copy")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => downloadJson(`${testId}.json`, pretty)}
            data-testid={`${testId}-download`}
          >
            {t("admin.ai.audit.detail.download")}
          </Button>
        </div>
      </div>
      <pre
        className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border border-hairline bg-bg p-3 font-mono text-xs text-ink"
        data-testid={testId}
      >
        {pretty}
      </pre>
    </div>
  );
}

const PII_KIND_LABEL_KEYS: Record<string, string> = {
  EMAIL: "admin.ai.audit.piiKind.EMAIL",
  MAC: "admin.ai.audit.piiKind.MAC",
  IPV4: "admin.ai.audit.piiKind.IPV4",
  IPV6: "admin.ai.audit.piiKind.IPV6",
  PHONE: "admin.ai.audit.piiKind.PHONE",
};

function PiiSummaryChip({ counts }: { counts: Record<string, number> | null }) {
  const { t } = useTranslation();
  if (!counts || Object.keys(counts).length === 0) return null;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const parts = Object.entries(counts).map(
    ([kind, n]) =>
      `${n} ${t(PII_KIND_LABEL_KEYS[kind] ?? "admin.ai.audit.piiKind.OTHER", { kind })}`,
  );
  return (
    <Badge tone="warn" data-testid="ai-audit-pii-summary">
      {t("admin.ai.audit.detail.piiSummary", { count: total })}:{" "}
      {parts.join(" · ")}
    </Badge>
  );
}

function AuditDetailDrawer({
  entryId,
  onClose,
}: {
  entryId: number;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const [tab, setTab] = useState<"messages" | "raw">("messages");
  const [piiVisible, setPiiVisible] = useState(false);
  const [piiMap, setPiiMap] = useState<Record<string, string> | null>(null);

  const detailQ = useQuery({
    queryKey: [...QUERY_KEY, "detail", entryId],
    queryFn: ({ signal }) => aiApi.getAuditLogEntry(entryId, signal),
  });

  const revealMutation = useMutation({
    mutationFn: () => aiApi.revealAuditPii(entryId),
    onSuccess: (res) => {
      setPiiMap(res.mapping);
      setPiiVisible(true);
    },
  });

  const entry = detailQ.data;
  const parsedRequest = entry
    ? safeParse<ParsedRequest>(entry.request_json)
    : null;
  const parsedResponse = entry
    ? safeParse<ParsedResponse>(entry.response_json)
    : null;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/30"
      data-testid="ai-audit-drawer-backdrop"
      onClick={onClose}
    >
      <aside
        className="flex h-full w-full max-w-2xl flex-col border-l border-hairline bg-surface shadow-xl"
        data-testid="ai-audit-drawer"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
          <h2 className="font-display text-base font-semibold text-ink">
            {t("admin.ai.audit.detail.title")} #{entryId}
          </h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            data-testid="ai-audit-drawer-close"
          >
            ✕
          </Button>
        </div>

        {detailQ.isLoading || !entry ? (
          <div className="flex flex-1 items-center justify-center">
            <Spinner />
          </div>
        ) : (
          <div className="flex-1 space-y-3 overflow-y-auto p-4">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone={featureTone(entry.feature)}>{entry.feature}</Badge>
              <Badge tone={statusTone(entry)}>
                {entry.status_code ??
                  (entry.error ? t("admin.ai.audit.error") : "—")}
              </Badge>
              {entry.provider_name && (
                <Badge tone="muted">{entry.provider_name}</Badge>
              )}
              {entry.model && <Badge tone="muted">{entry.model}</Badge>}
              <PiiSummaryChip counts={entry.pii_counts} />
            </div>

            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
              <DetailRow
                label={t("admin.ai.audit.table.time")}
                value={formatDateTime(entry.ts, locale)}
              />
              <DetailRow
                label={t("admin.ai.audit.table.ticket")}
                value={
                  entry.ticket_id != null ? (
                    <Link
                      to="/agent/tickets/$ticketId"
                      params={{ ticketId: String(entry.ticket_id) }}
                      className="text-accent hover:underline"
                    >
                      #{entry.ticket_id}
                    </Link>
                  ) : (
                    "—"
                  )
                }
              />
              <DetailRow
                label={t("admin.ai.audit.detail.runId")}
                value={entry.run_id ?? "—"}
                mono
              />
              <DetailRow
                label={t("admin.ai.audit.detail.trigger")}
                value={entry.trigger ?? "—"}
              />
              <DetailRow
                label={t("admin.ai.audit.table.tokens")}
                value={`${entry.prompt_tokens ?? 0} / ${entry.completion_tokens ?? 0}`}
                mono
              />
              <DetailRow
                label={t("admin.ai.audit.table.duration")}
                value={`${entry.duration_ms} ms`}
                mono
              />
            </dl>

            {entry.pii_counts && Object.keys(entry.pii_counts).length > 0 && (
              <label className="flex items-center gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={piiVisible}
                  disabled={revealMutation.isPending}
                  data-testid="ai-audit-pii-toggle"
                  onChange={(e) => {
                    if (e.target.checked) {
                      if (piiMap) setPiiVisible(true);
                      else revealMutation.mutate();
                    } else {
                      setPiiVisible(false);
                    }
                  }}
                />
                {t("admin.ai.audit.detail.revealPii")}
                {revealMutation.isPending && <Spinner className="h-3 w-3" />}
              </label>
            )}
            {revealMutation.isError && (
              <p className="text-xs text-danger">
                {t("admin.ai.audit.detail.revealPiiError")}
              </p>
            )}

            <Tabs
              items={[
                {
                  id: "messages",
                  label: t("admin.ai.audit.detail.tabMessages"),
                },
                { id: "raw", label: t("admin.ai.audit.detail.tabRaw") },
              ]}
              value={tab}
              onChange={(id) => setTab(id as "messages" | "raw")}
            />

            {tab === "messages" ? (
              <MessagesTab
                parsedRequest={parsedRequest}
                parsedResponse={parsedResponse}
                entry={entry}
                piiMap={piiVisible ? piiMap : null}
              />
            ) : (
              <div className="space-y-4">
                <RawJsonBox
                  label={t("admin.ai.audit.detail.rawRequest")}
                  raw={entry.request_json}
                  testId="ai-audit-raw-request"
                />
                <RawJsonBox
                  label={t("admin.ai.audit.detail.rawResponse")}
                  raw={entry.response_json}
                  testId="ai-audit-raw-response"
                />
              </div>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}

function DetailRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}) {
  return (
    <>
      <dt className="text-xs font-medium uppercase tracking-wide text-muted">
        {label}
      </dt>
      <dd className={cn("break-all text-ink", mono && "font-mono text-xs")}>
        {value}
      </dd>
    </>
  );
}

function RetentionFooter() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("30");

  const settingsQ = useQuery({
    queryKey: ["admin", "ai", "settings"],
    queryFn: ({ signal }) => aiApi.getSettings(signal),
  });

  const saveMutation = useMutation({
    mutationFn: (days: number) =>
      aiApi.putSettings({ audit_retention_days: days }),
    onSuccess: () => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: ["admin", "ai", "settings"] });
    },
  });

  const days = settingsQ.data?.audit_retention_days;

  return (
    <div
      className="flex items-center gap-2 text-xs text-muted"
      data-testid="ai-audit-retention"
    >
      {editing ? (
        <>
          <span>{t("admin.ai.audit.retentionEdit")}</span>
          <input
            type="number"
            min={1}
            max={365}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="w-20 rounded border border-hairline bg-bg px-2 py-1 text-ink"
            data-testid="ai-audit-retention-input"
          />
          <Button
            variant="primary"
            size="sm"
            disabled={saveMutation.isPending}
            onClick={() => saveMutation.mutate(Number(value))}
            data-testid="ai-audit-retention-save"
          >
            {t("admin.ai.audit.retentionSave")}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
            {t("admin.ai.audit.retentionCancel")}
          </Button>
        </>
      ) : (
        <>
          <span>
            {days != null
              ? t("admin.ai.audit.retention", { count: days })
              : "…"}
          </span>
          <button
            type="button"
            className="text-accent hover:underline"
            data-testid="ai-audit-retention-edit"
            onClick={() => {
              setValue(String(days ?? 30));
              setEditing(true);
            }}
          >
            {t("admin.ai.audit.retentionChange")}
          </button>
        </>
      )}
    </div>
  );
}

export function AiAuditPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const [preset, setPreset] = useState<PeriodPreset>("7d");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [providerId, setProviderId] = useState<number | "">("");
  const [feature, setFeature] = useState<AuditFeature | "">("");
  const [status, setStatus] = useState<AuditRequestStatus | "">("");
  const [ticket, setTicket] = useState("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const providersQ = useQuery({
    queryKey: ["admin", "ai", "providers"],
    queryFn: ({ signal }) => aiApi.listProviders(signal),
  });

  const filters: AiAuditLogFilterParams = useMemo(() => {
    const from =
      preset === "today"
        ? startOfTodayIso()
        : preset === "7d"
          ? daysAgoIso(7)
          : preset === "30d"
            ? daysAgoIso(30)
            : customFrom
              ? new Date(customFrom).toISOString()
              : undefined;
    const to =
      preset === "custom" && customTo
        ? new Date(customTo).toISOString()
        : undefined;
    return {
      from,
      to,
      provider_id: providerId === "" ? undefined : providerId,
      feature: feature === "" ? undefined : feature,
      status: status === "" ? undefined : status,
      ticket: ticket.trim() || undefined,
    };
  }, [preset, customFrom, customTo, providerId, feature, status, ticket]);

  const listQ = useQuery({
    queryKey: [...QUERY_KEY, "list", filters, page],
    queryFn: ({ signal }) =>
      aiApi.listAuditLog({ ...filters, page, page_size: PAGE_SIZE }, signal),
    // New requests keep arriving while the admin watches — poll the first
    // page (visible tabs only, react-query default) instead of requiring a
    // manual reload; fresh rows animate in (see newIds below).
    refetchInterval: page === 1 ? 15_000 : false,
  });

  // Rows that arrived through a background refetch get a one-shot entrance
  // animation. Tracks the highest id ever seen; only ids above it count as
  // "new" (paging back and forth does not re-animate old rows).
  const prevMaxIdRef = useRef<number | null>(null);
  const [newIds, setNewIds] = useState<ReadonlySet<number>>(new Set());
  useEffect(() => {
    const ids = (listQ.data?.items ?? []).map((i) => i.id);
    if (ids.length === 0) return;
    const maxId = Math.max(...ids);
    const prev = prevMaxIdRef.current;
    if (prev != null && maxId > prev && page === 1) {
      setNewIds(new Set(ids.filter((id) => id > prev)));
    }
    prevMaxIdRef.current = Math.max(prev ?? 0, maxId);
  }, [listQ.data, page]);

  const statsQ = useQuery({
    queryKey: [...QUERY_KEY, "stats", filters],
    queryFn: ({ signal }) => aiApi.getAuditLogStats(filters, signal),
  });

  const presetItems: SelectMenuItem<PeriodPreset>[] = [
    { value: "today", label: t("admin.ai.audit.periodToday") },
    { value: "7d", label: t("admin.ai.audit.period7d") },
    { value: "30d", label: t("admin.ai.audit.period30d") },
    { value: "custom", label: t("admin.ai.audit.periodCustom") },
  ];
  const providerItems: SelectMenuItem<number | "">[] = [
    { value: "", label: t("admin.ai.audit.allProviders") },
    ...(providersQ.data?.items ?? []).map((p) => ({
      value: p.id,
      label: p.name,
    })),
  ];
  const featureItems: SelectMenuItem<AuditFeature | "">[] = [
    { value: "", label: t("admin.ai.audit.allFeatures") },
    { value: "draft", label: t("admin.ai.audit.feature.draft") },
    { value: "summary", label: t("admin.ai.audit.feature.summary") },
    { value: "auto_reply", label: t("admin.ai.audit.feature.auto_reply") },
    { value: "vision", label: t("admin.ai.audit.feature.vision") },
    { value: "test", label: t("admin.ai.audit.feature.test") },
  ];
  const statusItems: SelectMenuItem<AuditRequestStatus | "">[] = [
    { value: "", label: t("admin.ai.audit.allStatuses") },
    { value: "ok", label: t("admin.ai.audit.statusOk") },
    { value: "error", label: t("admin.ai.audit.error") },
  ];

  const items: AiAuditLogListItemOut[] = listQ.data?.items ?? [];
  const total = listQ.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const stats = statsQ.data;
  const statsCost = {
    total: stats?.total_cost ?? null,
    currency: stats?.cost_currency ?? null,
  };

  return (
    <div className="flex flex-col gap-4" data-testid="ai-audit-page">
      <div>
        <h1 className="flex items-center gap-1.5 font-display text-xl font-semibold text-ink">
          {t("admin.ai.audit.title")}
          <HelpPopover
            title={t("admin.ai.audit.title")}
            testId="ai-audit-help-title"
          >
            {t("admin.help.ai.audit.overview")}
          </HelpPopover>
        </h1>
        <p className="mt-1 text-sm text-muted">
          {t("admin.ai.audit.subtitle")}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard
          label={t("admin.ai.audit.stat.requests")}
          value={stats ? String(stats.total_requests) : "…"}
          testId="ai-audit-stat-requests"
        />
        <StatCard
          label={t("admin.ai.audit.stat.tokens")}
          value={
            stats
              ? tokensLabel(
                  stats.total_prompt_tokens + stats.total_completion_tokens,
                )
              : "…"
          }
          hint={
            stats
              ? `${tokensLabel(stats.total_prompt_tokens)} in / ${tokensLabel(stats.total_completion_tokens)} out`
              : undefined
          }
          testId="ai-audit-stat-tokens"
        />
        <StatCard
          label={t("admin.ai.audit.stat.cost")}
          value={
            stats
              ? statsCost.total != null
                ? formatCost(statsCost.total, statsCost.currency, locale)
                : "—"
              : "…"
          }
          hint={
            stats && statsCost.total == null
              ? t("admin.ai.audit.stat.costUnavailable")
              : undefined
          }
          testId="ai-audit-stat-cost"
        />
        <StatCard
          label={t("admin.ai.audit.stat.errorRate")}
          value={stats ? `${(stats.error_rate * 100).toFixed(1)}%` : "…"}
          tone={stats && stats.error_rate > 0 ? "danger" : undefined}
          testId="ai-audit-stat-error-rate"
        />
        <StatCard
          label={t("admin.ai.audit.stat.topModel")}
          value={stats?.top_model ?? "—"}
          testId="ai-audit-stat-top-model"
        />
      </div>

      {stats && stats.per_day.length > 0 && (
        <PerDayChart perDay={stats.per_day} />
      )}

      <div
        className="flex flex-wrap items-end gap-2 rounded border border-hairline bg-surface p-3"
        data-testid="ai-audit-filters"
      >
        <label className="flex flex-col gap-1 text-xs text-muted">
          {t("admin.ai.audit.period")}
          <SelectMenu
            items={presetItems}
            value={preset}
            onSelect={(v) => {
              setPreset(v);
              setPage(1);
            }}
            panelTestId="ai-audit-filter-period-panel"
            trigger={({ open, ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ai-audit-filter-period"
                {...toggleProps}
                className="flex min-w-[9rem] items-center justify-between gap-2 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              >
                <span>
                  {presetItems.find((i) => i.value === preset)?.label}
                </span>
                <ChevronDownIcon
                  className={cn(
                    "shrink-0 text-muted transition-transform duration-150",
                    open && "rotate-180",
                  )}
                />
              </button>
            )}
          />
        </label>
        {preset === "custom" && (
          <>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("admin.mailLog.from")}
              <input
                type="datetime-local"
                className="rounded border border-hairline bg-bg px-2 py-1.5 text-sm text-ink"
                value={customFrom}
                onChange={(e) => {
                  setCustomFrom(e.target.value);
                  setPage(1);
                }}
                data-testid="ai-audit-filter-from"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("admin.mailLog.to")}
              <input
                type="datetime-local"
                className="rounded border border-hairline bg-bg px-2 py-1.5 text-sm text-ink"
                value={customTo}
                onChange={(e) => {
                  setCustomTo(e.target.value);
                  setPage(1);
                }}
                data-testid="ai-audit-filter-to"
              />
            </label>
          </>
        )}
        <label className="flex flex-col gap-1 text-xs text-muted">
          {t("admin.ai.audit.provider")}
          <SelectMenu
            items={providerItems}
            value={providerId}
            onSelect={(v) => {
              setProviderId(v);
              setPage(1);
            }}
            panelTestId="ai-audit-filter-provider-panel"
            trigger={({ open, ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ai-audit-filter-provider"
                {...toggleProps}
                className="flex min-w-[9rem] items-center justify-between gap-2 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              >
                <span className="truncate">
                  {providerItems.find((i) => i.value === providerId)?.label}
                </span>
                <ChevronDownIcon
                  className={cn(
                    "shrink-0 text-muted transition-transform duration-150",
                    open && "rotate-180",
                  )}
                />
              </button>
            )}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          {t("admin.ai.audit.feature.label")}
          <SelectMenu
            items={featureItems}
            value={feature}
            onSelect={(v) => {
              setFeature(v);
              setPage(1);
            }}
            panelTestId="ai-audit-filter-feature-panel"
            trigger={({ open, ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ai-audit-filter-feature"
                {...toggleProps}
                className="flex min-w-[9rem] items-center justify-between gap-2 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              >
                <span>
                  {featureItems.find((i) => i.value === feature)?.label}
                </span>
                <ChevronDownIcon
                  className={cn(
                    "shrink-0 text-muted transition-transform duration-150",
                    open && "rotate-180",
                  )}
                />
              </button>
            )}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          {t("admin.ai.audit.status")}
          <SelectMenu
            items={statusItems}
            value={status}
            onSelect={(v) => {
              setStatus(v);
              setPage(1);
            }}
            panelTestId="ai-audit-filter-status-panel"
            trigger={({ open, ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="ai-audit-filter-status"
                {...toggleProps}
                className="flex min-w-[8rem] items-center justify-between gap-2 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              >
                <span>
                  {statusItems.find((i) => i.value === status)?.label}
                </span>
                <ChevronDownIcon
                  className={cn(
                    "shrink-0 text-muted transition-transform duration-150",
                    open && "rotate-180",
                  )}
                />
              </button>
            )}
          />
        </label>
        <label className="flex min-w-[10rem] flex-1 flex-col gap-1 text-xs text-muted">
          {t("admin.ai.audit.ticketSearch")}
          <input
            data-testid="ai-audit-filter-ticket"
            type="search"
            className="rounded border border-hairline bg-bg px-2 py-1.5 text-sm text-ink"
            value={ticket}
            placeholder={t("admin.ai.audit.ticketSearchPlaceholder")}
            onChange={(e) => {
              setTicket(e.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>

      <div className="overflow-x-auto rounded border border-hairline bg-surface">
        {listQ.isLoading ? (
          <div className="flex justify-center p-8">
            <Spinner />
          </div>
        ) : items.length === 0 ? (
          <p className="p-6 text-sm text-muted" data-testid="ai-audit-empty">
            {t("admin.ai.audit.empty")}
          </p>
        ) : (
          <table
            className="w-full min-w-[64rem] text-left text-sm"
            data-testid="ai-audit-table"
          >
            <thead className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">
                  {t("admin.ai.audit.table.time")}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t("admin.ai.audit.table.provider")}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t("admin.ai.audit.table.feature")}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t("admin.ai.audit.table.ticket")}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t("admin.ai.audit.table.tokens")}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t("admin.ai.audit.table.cost")}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t("admin.ai.audit.table.duration")}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t("admin.ai.audit.table.pii")}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t("admin.ai.audit.table.status")}
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => {
                const piiTotal = row.pii_counts
                  ? Object.values(row.pii_counts).reduce((a, b) => a + b, 0)
                  : 0;
                return (
                  <tr
                    key={row.id}
                    data-testid={`ai-audit-row-${row.id}`}
                    className={cn(
                      "cursor-pointer border-b border-hairline last:border-0 hover:bg-surface-subtle",
                      newIds.has(row.id) && "animate-audit-row-in",
                    )}
                    onClick={() => setSelectedId(row.id)}
                  >
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted">
                      {formatDateTime(row.ts, locale)}
                    </td>
                    <td className="max-w-[10rem] truncate px-3 py-2 text-xs">
                      <span className="text-ink">
                        {row.provider_name || "—"}
                      </span>
                      <span className="text-muted"> / {row.model || "—"}</span>
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={featureTone(row.feature)}>
                        {row.feature}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">
                      {row.ticket_id != null ? (
                        <Link
                          to="/agent/tickets/$ticketId"
                          params={{ ticketId: String(row.ticket_id) }}
                          className="font-mono text-xs text-accent hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          #{row.ticket_id}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted">
                      {row.prompt_tokens ?? 0} / {row.completion_tokens ?? 0}
                    </td>
                    <td
                      className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted"
                      data-testid={`ai-audit-row-${row.id}-cost`}
                    >
                      {row.cost != null
                        ? formatCost(row.cost, row.cost_currency, locale)
                        : "—"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted">
                      {row.duration_ms} ms
                    </td>
                    <td className="px-3 py-2">
                      {piiTotal > 0 ? (
                        <Badge
                          tone="warn"
                          data-testid={`ai-audit-row-${row.id}-pii`}
                        >
                          {piiTotal}
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={statusTone(row)}>
                        {row.status_code ?? t("admin.ai.audit.error")}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between gap-2 text-sm text-muted">
          <span>
            {total} {t("admin.mailLog.entries")}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              data-testid="ai-audit-prev"
            >
              ←
            </Button>
            <span>
              {page} / {totalPages}
            </span>
            <Button
              variant="ghost"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              data-testid="ai-audit-next"
            >
              →
            </Button>
          </div>
        </div>
      )}

      <div className="flex items-center gap-1.5">
        <RetentionFooter />
        <HelpPopover
          title={t("admin.ai.audit.retentionChange")}
          testId="ai-audit-help-retention"
        >
          {t("admin.help.ai.audit.retention")}
        </HelpPopover>
      </div>

      {selectedId != null && (
        <AuditDetailDrawer
          entryId={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
