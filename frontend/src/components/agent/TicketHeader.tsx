import { useTranslation } from "react-i18next";
import type { TicketDetail } from "@/lib/api";
import { formatDateTime, isEscalated } from "@/lib/format";
import { Badge } from "@/components/ui/Badge";
import {
  combinedEscalationLevel,
  formatCountdown,
  spineClassName,
  stateColorVar,
} from "@/lib/status";
import type { CSSProperties } from "react";

/** Priority text tone by Znuny priority id (1=lowest … 5=highest). */
function priorityTextClass(priorityId: number | null | undefined): string {
  if (priorityId == null) return "text-ink";
  if (priorityId >= 5) return "text-danger";
  if (priorityId === 4) return "text-warn";
  return "text-ink";
}

/** Drop Znuny's leading numeric rank ("3 normal" → "normal"). */
function priorityName(priority: string | null | undefined): string | null {
  if (!priority) return null;
  return priority.replace(/^\s*\d+\s+/, "");
}

export function TicketHeader({ ticket }: { ticket: TicketDetail }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const badges: { label: string; tone: "danger" | "warn" }[] = [];
  if (isEscalated(ticket.escalation_response_time)) {
    badges.push({ label: t("ticket.escResponse"), tone: "danger" });
  }
  if (isEscalated(ticket.escalation_update_time)) {
    badges.push({ label: t("ticket.escUpdate"), tone: "danger" });
  }
  if (isEscalated(ticket.escalation_solution_time)) {
    badges.push({ label: t("ticket.escSolution"), tone: "danger" });
  }
  if (isEscalated(ticket.escalation_time) && badges.length === 0) {
    badges.push({ label: t("ticket.escalated"), tone: "danger" });
  }

  const escLevel = combinedEscalationLevel([
    ticket.escalation_time,
    ticket.escalation_response_time,
    ticket.escalation_update_time,
    ticket.escalation_solution_time,
  ]);
  const spineColor = escLevel === "none" ? stateColorVar(ticket.state) : undefined;
  const countdown =
    escLevel !== "none"
      ? formatCountdown(
          ticket.escalation_time ??
            ticket.escalation_response_time ??
            ticket.escalation_update_time ??
            ticket.escalation_solution_time,
        )
      : null;

  return (
    <header
      className={`space-y-2 rounded-lg border border-hairline bg-surface p-3.5 pl-5 ${spineClassName(
        escLevel,
      )}`}
      style={{ "--spine-color": spineColor } as CSSProperties}
      data-testid="ticket-header"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm text-accent">{ticket.tn}</span>
            {countdown && (
              <span className="rounded bg-escalation/15 px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-escalation">
                {countdown}
              </span>
            )}
            {badges.map((b) => (
              <Badge key={b.label} tone={b.tone}>
                {b.label}
              </Badge>
            ))}
            {ticket.lock && ticket.lock.toLowerCase() !== "unlock" && (
              <Badge tone="warn">{ticket.lock}</Badge>
            )}
          </div>
          <h1 className="mt-1 font-display text-xl font-semibold text-ink">
            {ticket.title || t("ticket.noTitle")}
          </h1>
        </div>
      </div>
      {/* One compact metadata line — no redundant badge row, priority without
          its numeric rank. Wraps on narrow screens. */}
      <dl
        className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm"
        data-testid="ticket-meta-line"
      >
        <Meta label={t("ticket.state")} value={ticket.state} dot={stateColorVar(ticket.state)} />
        <Meta
          label={t("ticket.priority")}
          value={priorityName(ticket.priority)}
          valueClass={priorityTextClass(ticket.priority_id)}
        />
        <Meta label={t("ticket.queue")} value={ticket.queue_name} />
        <Meta label={t("ticket.owner")} value={ticket.owner_name || ticket.owner_login} />
        <Meta label={t("ticket.customer")} value={ticket.customer_user_id || ticket.customer_id} />
        <Meta label={t("ticket.created")} value={formatDateTime(ticket.create_time, locale)} />
        <Meta label={t("ticket.changed")} value={formatDateTime(ticket.change_time, locale)} />
      </dl>
      {ticket.dynamic_fields && ticket.dynamic_fields.length > 0 && (
        <details className="rounded border border-hairline bg-surface-subtle px-3 py-2 text-sm">
          <summary className="cursor-pointer font-medium text-muted">
            {t("ticket.dynamicFields")}
          </summary>
          <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {ticket.dynamic_fields.map((df) => (
              <div key={df.name}>
                <dt className="text-xs text-muted">{df.label || df.name}</dt>
                <dd className="text-ink">
                  {(df.values ?? []).map(String).join(", ") || "—"}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </header>
  );
}

function Meta({
  label,
  value,
  dot,
  valueClass,
}: {
  label: string;
  value: string | null | undefined;
  dot?: string;
  valueClass?: string;
}) {
  if (!value) return null;
  return (
    <div className="inline-flex items-center gap-1.5">
      {dot && (
        <span className="inline-block h-2 w-2 rounded-full" style={{ background: dot }} />
      )}
      <dt className="text-[11px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className={`font-medium ${valueClass ?? "text-ink"}`}>{value}</dd>
    </div>
  );
}
