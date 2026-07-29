import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  api,
  type MailLogDirection,
  type MailLogListParams,
  type MailLogOut,
  type MailLogStatus,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { ChevronDownIcon } from "@/components/ui/icons";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

const QUERY_KEY = ["admin", "mail", "log"] as const;

function statusTone(status: string): "success" | "danger" | "warn" | "muted" | "accent" {
  switch (status) {
    case "sent":
    case "received":
      return "success";
    case "failed":
      return "danger";
    case "filtered":
      return "warn";
    case "queued":
      return "muted";
    default:
      return "accent";
  }
}

function DirectionIcon({ direction }: { direction: string }) {
  const isOut = direction === "out";
  return (
    <span
      className={cn(
        "inline-flex h-6 w-6 items-center justify-center rounded border text-xs font-semibold",
        isOut
          ? "border-accent/30 bg-accent-dim text-accent"
          : "border-green/30 bg-green/15 text-green",
      )}
      title={isOut ? "out" : "in"}
      data-testid={`mail-log-dir-${direction}`}
    >
      {isOut ? "↑" : "↓"}
    </span>
  );
}

export function MailLogPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const [direction, setDirection] = useState<MailLogDirection | "">("");
  const [status, setStatus] = useState<MailLogStatus | "">("");
  const [q, setQ] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<MailLogOut | null>(null);

  const listParams: MailLogListParams = useMemo(
    () => ({
      page,
      pageSize: 25,
      direction: direction || null,
      status: status || null,
      q: q.trim() || null,
      from: from ? new Date(from).toISOString() : null,
      to: to ? new Date(to).toISOString() : null,
    }),
    [page, direction, status, q, from, to],
  );

  const listQ = useQuery({
    queryKey: [...QUERY_KEY, listParams],
    queryFn: ({ signal }) => api.listMailLog(listParams, signal),
  });

  const directionItems: SelectMenuItem<MailLogDirection | "">[] = [
    { value: "", label: t("admin.mailLog.all") },
    { value: "in", label: t("admin.mailLog.dirIn") },
    { value: "out", label: t("admin.mailLog.dirOut") },
  ];
  const statusItems: SelectMenuItem<MailLogStatus | "">[] = [
    { value: "", label: t("admin.mailLog.all") },
    { value: "sent", label: "sent" },
    { value: "failed", label: "failed" },
    { value: "queued", label: "queued" },
    { value: "received", label: "received" },
    { value: "filtered", label: "filtered" },
  ];

  const detailQ = useQuery({
    queryKey: [...QUERY_KEY, "detail", selected?.id],
    queryFn: ({ signal }) => api.getMailLog(selected!.id, signal),
    enabled: selected != null,
  });

  const items = listQ.data?.items ?? [];
  const total = listQ.data?.total ?? 0;
  const pageSize = listQ.data?.page_size ?? 25;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const detail = detailQ.data ?? selected;

  return (
    <div className="flex flex-col gap-4" data-testid="mail-log-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.mailLog.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.mailLog.subtitle")}</p>
      </div>

      <div
        className="flex flex-wrap items-end gap-2 rounded border border-hairline bg-surface p-3"
        data-testid="mail-log-filters"
      >
        <label className="flex flex-col gap-1 text-xs text-muted">
          {t("admin.mailLog.direction")}
          <SelectMenu
            items={directionItems}
            value={direction}
            onSelect={(v) => {
              setDirection(v);
              setPage(1);
            }}
            panelTestId="mail-log-filter-direction-panel"
            trigger={({ open, ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="mail-log-filter-direction"
                {...toggleProps}
                className="flex min-w-[8rem] items-center justify-between gap-2 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              >
                <span>{directionItems.find((i) => i.value === direction)?.label}</span>
                <ChevronDownIcon
                  className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")}
                />
              </button>
            )}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          {t("admin.mailLog.status")}
          <SelectMenu
            items={statusItems}
            value={status}
            onSelect={(v) => {
              setStatus(v);
              setPage(1);
            }}
            panelTestId="mail-log-filter-status-panel"
            trigger={({ open, ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                data-testid="mail-log-filter-status"
                {...toggleProps}
                className="flex min-w-[8rem] items-center justify-between gap-2 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              >
                <span>{statusItems.find((i) => i.value === status)?.label}</span>
                <ChevronDownIcon
                  className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")}
                />
              </button>
            )}
          />
        </label>
        <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-xs text-muted">
          {t("admin.mailLog.search")}
          <input
            data-testid="mail-log-filter-q"
            type="search"
            className="rounded border border-hairline bg-bg px-2 py-1.5 text-sm text-ink"
            value={q}
            placeholder={t("admin.mailLog.searchPlaceholder")}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          {t("admin.mailLog.from")}
          <input
            data-testid="mail-log-filter-from"
            type="datetime-local"
            className="rounded border border-hairline bg-bg px-2 py-1.5 text-sm text-ink"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          {t("admin.mailLog.to")}
          <input
            data-testid="mail-log-filter-to"
            type="datetime-local"
            className="rounded border border-hairline bg-bg px-2 py-1.5 text-sm text-ink"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>

      {/* Split view ("Variante C"): message list left, fixed detail panel
          right — like a mail client, no overlaying drawer. ArrowUp/Down move
          the selection along the list; the detail follows. */}
      <div className="grid items-start gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="overflow-hidden rounded-xl border border-hairline bg-surface">
          {listQ.isLoading ? (
            <div className="flex justify-center p-8">
              <Spinner />
            </div>
          ) : items.length === 0 ? (
            <p className="p-6 text-sm text-muted" data-testid="mail-log-empty">
              {t("admin.mailLog.empty")}
            </p>
          ) : (
            <div
              data-testid="mail-log-table"
              role="listbox"
              aria-label={t("admin.mailLog.title")}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
                e.preventDefault();
                const idx = items.findIndex((r) => r.id === selected?.id);
                const next =
                  e.key === "ArrowDown"
                    ? items[Math.min(idx + 1, items.length - 1)]
                    : items[Math.max(idx - 1, 0)];
                if (next) setSelected(next);
              }}
              className="focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
            >
              {items.map((row) => {
                const isSel = selected?.id === row.id;
                return (
                  <div
                    key={row.id}
                    role="option"
                    aria-selected={isSel}
                    data-testid={`mail-log-row-${row.id}`}
                    className={cn(
                      "flex cursor-pointer items-center gap-3 border-b border-hairline px-3.5 py-2.5 last:border-0",
                      isSel
                        ? "bg-accent-dim shadow-[inset_3px_0_0_0] shadow-accent"
                        : "hover:bg-surface-subtle",
                    )}
                    onClick={() => setSelected(row)}
                  >
                    <DirectionIcon direction={row.direction} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13.5px] font-semibold text-ink">
                        {row.subject || "—"}
                      </div>
                      <div className="truncate font-mono text-xs text-muted">
                        {row.from_addr || "—"}
                        <span className="opacity-60"> → </span>
                        {row.to_addr || "—"}
                      </div>
                    </div>
                    {row.ticket_id != null && (
                      <Link
                        to="/agent/tickets/$ticketId"
                        params={{ ticketId: String(row.ticket_id) }}
                        className="shrink-0 font-mono text-xs font-semibold text-accent hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        #{row.ticket_id}
                      </Link>
                    )}
                    <Badge
                      tone={statusTone(row.status)}
                      data-testid={`mail-log-status-${row.status}`}
                    >
                      {row.status}
                    </Badge>
                    <span
                      className="shrink-0 text-right font-mono text-[11px] tabular-nums text-muted"
                      title={formatDateTime(row.created_at, locale)}
                    >
                      {formatDateTime(row.created_at, locale)}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {selected != null && detail != null ? (
          <aside
            className="sticky top-4 flex max-h-[calc(100vh-2rem)] flex-col overflow-hidden rounded-xl border border-hairline bg-surface"
            data-testid="mail-log-drawer"
          >
            <div className="flex items-start justify-between gap-3 border-b border-hairline px-4 py-3">
              <div className="min-w-0">
                <h2 className="truncate font-display text-[15px] font-semibold text-ink">
                  {detail.subject || `${t("admin.mailLog.detail")} #${detail.id}`}
                </h2>
                <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="inline-flex items-center gap-1.5">
                    <DirectionIcon direction={detail.direction} />
                    {detail.direction === "out"
                      ? t("admin.mailLog.dirOut")
                      : t("admin.mailLog.dirIn")}
                  </span>
                  <Badge tone={statusTone(detail.status)}>{detail.status}</Badge>
                  <span className="font-mono tabular-nums">
                    {formatDateTime(detail.created_at, locale)}
                  </span>
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelected(null)}
                data-testid="mail-log-drawer-close"
              >
                ✕
              </Button>
            </div>
            <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
              <DetailRow label={t("admin.mailLog.from")} value={detail.from_addr || "—"} mono />
              <DetailRow label={t("admin.mailLog.to")} value={detail.to_addr || "—"} mono />
              <DetailRow label="Cc" value={detail.cc_addr || "—"} mono />
              <DetailRow label="Message-ID" value={detail.message_id || "—"} mono />
              <DetailRow
                label={t("admin.mailLog.ticket")}
                value={
                  detail.ticket_id != null ? (
                    <Link
                      to="/agent/tickets/$ticketId"
                      params={{ ticketId: String(detail.ticket_id) }}
                      className="text-accent hover:underline"
                    >
                      #{detail.ticket_id}
                    </Link>
                  ) : (
                    "—"
                  )
                }
              />
              <DetailRow
                label="Article"
                value={detail.article_id != null ? String(detail.article_id) : "—"}
                mono
              />
              <DetailRow label={t("admin.mailLog.queue")} value={detail.queue || "—"} />
              <DetailRow
                label="SMTP"
                value={detail.smtp_code != null ? String(detail.smtp_code) : "—"}
                mono
              />
              <DetailRow
                label={t("admin.mailLog.duration")}
                value={detail.duration_ms != null ? `${detail.duration_ms} ms` : "—"}
              />
              <div>
                <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
                  {t("admin.mailLog.detailField")}
                </div>
                <pre
                  className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-hairline bg-bg p-3 font-mono text-xs text-ink"
                  data-testid="mail-log-detail-body"
                >
                  {detail.detail || "—"}
                </pre>
              </div>
            </div>
          </aside>
        ) : (
          <div
            className="sticky top-4 hidden min-h-[16rem] items-center justify-center rounded-xl border border-dashed border-hairline bg-surface p-6 text-center text-sm text-muted lg:flex"
            data-testid="mail-log-detail-placeholder"
          >
            {t("admin.mailLog.selectPrompt")}
          </div>
        )}
      </div>

      {total > pageSize && (
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
              data-testid="mail-log-prev"
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
              data-testid="mail-log-next"
            >
              →
            </Button>
          </div>
        </div>
      )}

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
    <div className="grid grid-cols-[7rem_1fr] gap-2">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className={cn("break-all text-ink", mono && "font-mono text-xs")}>{value}</div>
    </div>
  );
}
