import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { PhoneIcon } from "@/components/ui/icons";
import { phoneApi, type CallerCustomer, type CallerTicket } from "@/lib/phoneApi";
import { stateLabel } from "@/lib/status";

const FIELD_CLASS =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-[13.5px] text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";

/** Digits in a typed number — the lookup needs five. */
function digitCount(value: string): number {
  return value.replace(/\D/g, "").length;
}

/**
 * Caller-number field of the phone ticket form with a live lookup
 * (`GET /reference/caller`): every customer whose phone/mobile matches, to
 * pick as the ticket's customer, and their open tickets — a call about one
 * of those is logged there instead of opening a new ticket.
 */
export function CallerLookup({
  number,
  onNumberChange,
  showCustomers,
  onPickCustomer,
  onLogOnTicket,
}: {
  number: string;
  onNumberChange: (value: string) => void;
  /** Hidden once a customer is chosen; the open tickets stay. */
  showCustomers: boolean;
  onPickCustomer: (customer: CallerCustomer) => void;
  onLogOnTicket: (ticket: CallerTicket) => void;
}) {
  const { t } = useTranslation();
  const [debounced, setDebounced] = useState(number);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(number), 300);
    return () => window.clearTimeout(handle);
  }, [number]);

  const lookupQ = useQuery({
    queryKey: ["reference", "caller", debounced.trim()],
    queryFn: ({ signal }) => phoneApi.callerLookup(debounced.trim(), signal),
    enabled: digitCount(debounced) >= 5,
  });
  const customers = lookupQ.data?.customers ?? [];
  const tickets = lookupQ.data?.open_tickets ?? [];

  return (
    <div className="space-y-2" data-testid="caller-lookup">
      <label className="block">
        <span className="mb-1 flex items-center gap-1 text-[12px] font-medium text-muted">
          <PhoneIcon className="h-3 w-3" /> {t("phone.callerNumber")}
        </span>
        <input
          type="tel"
          value={number}
          data-testid="caller-number"
          onChange={(e) => onNumberChange(e.target.value)}
          className={FIELD_CLASS}
          placeholder="+49 …"
        />
        <span className="mt-1 block text-[11px] text-muted">{t("phone.callerHint")}</span>
      </label>
      {digitCount(debounced) >= 5 && lookupQ.isLoading && (
        <div className="flex justify-center py-2">
          <Spinner />
        </div>
      )}
      {lookupQ.data && showCustomers && customers.length === 0 && (
        <p className="text-xs text-muted" data-testid="caller-no-match">
          {t("phone.callerNoMatch")}
        </p>
      )}
      {showCustomers && customers.length > 0 && (
        <div className="rounded border border-hairline" data-testid="caller-customers">
          <p className="border-b border-hairline px-3 py-1 text-[11px] font-medium uppercase tracking-wide text-muted">
            {t("phone.callerMatches")}
          </p>
          {customers.map((c) => (
            <div key={c.login} className="flex items-center gap-2 px-3 py-1.5 text-sm">
              <span className="min-w-0 flex-1 truncate">
                <span className="font-medium text-ink">{c.name || c.login}</span>{" "}
                <span className="text-muted">{c.email}</span>
                {c.company && <span className="text-muted"> · {c.company}</span>}
              </span>
              <span className="shrink-0 font-mono text-[11.5px] text-muted">{c.phone || c.mobile}</span>
              <button
                type="button"
                data-testid={`caller-pick-${c.login}`}
                onClick={() => onPickCustomer(c)}
                className="shrink-0 rounded border border-accent/40 px-2 py-0.5 text-xs text-accent hover:bg-accent-dim"
              >
                {t("phone.useCustomer")}
              </button>
            </div>
          ))}
        </div>
      )}
      {tickets.length > 0 && (
        <div className="rounded border border-hairline" data-testid="caller-tickets">
          <p className="border-b border-hairline px-3 py-1 text-[11px] font-medium uppercase tracking-wide text-muted">
            {t("phone.openTickets")}
          </p>
          {tickets.map((tk) => (
            <div key={tk.id} className="flex items-center gap-2 px-3 py-1.5 text-sm">
              <span className="shrink-0 font-mono text-[11.5px] text-muted">{tk.tn}</span>
              <span className="min-w-0 flex-1 truncate text-ink">{tk.title}</span>
              <Badge tone="muted" className="shrink-0">
                {stateLabel(t, tk.state)}
              </Badge>
              <span className="hidden shrink-0 text-xs text-muted sm:inline">{tk.queue}</span>
              <button
                type="button"
                data-testid={`caller-log-${tk.id}`}
                onClick={() => onLogOnTicket(tk)}
                className="shrink-0 rounded border border-hairline px-2 py-0.5 text-xs text-ink hover:bg-surface-subtle"
              >
                {t("phone.logOnTicket")}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
