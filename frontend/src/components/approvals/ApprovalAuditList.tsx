import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ApprovalEvent } from "@/lib/approvalsApi";
import { formatDateTime } from "@/lib/format";

/** Collapsed "Verlauf" of an approval: one line per audit event with a plain-language action label. */
export function ApprovalAuditList({ events }: { events: ApprovalEvent[] }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <section data-testid="approval-audit">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="text-xs font-medium text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        data-testid="approval-audit-toggle"
      >
        {t("approvals.audit.title", { count: events.length })}
      </button>
      {open &&
        (events.length === 0 ? (
          <p className="mt-2 text-xs text-muted">{t("approvals.audit.empty")}</p>
        ) : (
          <ul className="mt-2 space-y-1" data-testid="approval-audit-list">
            {events.map((ev, i) => (
              <li key={`${ev.at}-${i}`} className="flex flex-wrap gap-x-2 text-xs text-ink" data-testid="approval-audit-item">
                <span className="text-muted">{formatDateTime(ev.at, i18n.language)}</span>
                <span className="font-medium" data-testid="approval-audit-action">
                  {t(`approvals.audit.${ev.action}`, { defaultValue: ev.action })}
                </span>
                <span className="text-muted">{ev.actor_label}</span>
              </li>
            ))}
          </ul>
        ))}
    </section>
  );
}
