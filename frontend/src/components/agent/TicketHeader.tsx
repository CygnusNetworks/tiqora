import { useTranslation } from "react-i18next";
import type { TicketDetail } from "@/lib/api";
import { formatDateTime, isEscalated } from "@/lib/format";
import { Badge } from "@/components/ui/Badge";

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

  return (
    <header
      className="space-y-3 rounded-lg border border-border bg-surface-elevated p-4"
      data-testid="ticket-header"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm text-accent">{ticket.tn}</span>
            {badges.map((b) => (
              <Badge key={b.label} tone={b.tone}>
                {b.label}
              </Badge>
            ))}
            {ticket.lock && ticket.lock.toLowerCase() !== "unlock" && (
              <Badge tone="warn">{ticket.lock}</Badge>
            )}
          </div>
          <h1 className="mt-1 text-xl font-semibold text-ink">
            {ticket.title || t("ticket.noTitle")}
          </h1>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3 lg:grid-cols-4">
        <Meta label={t("ticket.state")} value={ticket.state} />
        <Meta label={t("ticket.priority")} value={ticket.priority} />
        <Meta label={t("ticket.queue")} value={ticket.queue_name} />
        <Meta
          label={t("ticket.owner")}
          value={ticket.owner_name || ticket.owner_login}
        />
        <Meta
          label={t("ticket.customer")}
          value={ticket.customer_user_id || ticket.customer_id}
        />
        <Meta
          label={t("ticket.created")}
          value={formatDateTime(ticket.create_time, locale)}
        />
        <Meta
          label={t("ticket.changed")}
          value={formatDateTime(ticket.change_time, locale)}
        />
      </dl>
      {ticket.dynamic_fields && ticket.dynamic_fields.length > 0 && (
        <details className="rounded border border-border bg-surface px-3 py-2 text-sm">
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
}: {
  label: string;
  value: string | null | undefined;
}) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="font-medium text-ink">{value || "—"}</dd>
    </div>
  );
}
