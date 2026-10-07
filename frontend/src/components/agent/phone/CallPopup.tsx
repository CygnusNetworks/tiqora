import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { PhoneIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import {
  callAnsweredAt,
  callDurationSeconds,
  callEndedAt,
  restoreCalls,
  useCalls,
  visibleCalls,
} from "@/lib/callPopup";
import { phoneApi, type ActiveCall, type CallerTicket } from "@/lib/phoneApi";
import { formatElapsed, requestPhoneCall } from "@/lib/phoneCall";
import { stateLabel } from "@/lib/status";
import { useConnectionStatus } from "@/lib/useSSE";
import { dismissCall, phoneTicketSearchForCall } from "./callTicket";

/** Digits in a number — the caller lookup needs five (as in `CallerLookup`). */
const digitCount = (value: string) => value.replace(/\D/g, "").length;

function CallCard({ call, now }: { call: ActiveCall; now: number }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const number = call.number.trim();
  const lookupQ = useQuery({
    queryKey: ["reference", "caller", number],
    queryFn: ({ signal }) => phoneApi.callerLookup(number, signal),
    enabled: digitCount(number) >= 5,
    staleTime: 60 * 1000,
  });
  const customers = lookupQ.data?.customers ?? [];
  const tickets = lookupQ.data?.open_tickets ?? [];
  const single = customers.length === 1 ? customers[0] : undefined;

  const outbound = call.direction === "outbound";
  const answered = callAnsweredAt(call) !== null;
  const ended = call.state === "ended";
  const title = ended
    ? answered
      ? t("callPopup.ended")
      : t("callPopup.missed")
    : call.state === "answered"
      ? t("callPopup.answered")
      : outbound
        ? t("callPopup.outgoing")
        : t("callPopup.ringing");
  const who = single
    ? single.name || single.login
    : customers.length > 1
      ? t("callPopup.severalCustomers", { count: customers.length })
      : number || t("callPopup.unknownNumber");

  const timing = {
    startedAt: callAnsweredAt(call),
    endedAt: callEndedAt(call),
  };

  const logOnTicket = (tk: CallerTicket) => {
    requestPhoneCall(tk.id, {
      direction: call.direction,
      number: number || null,
      ...timing,
    });
    dismissCall(call.call_id);
    void navigate({ to: "/agent/tickets/$ticketId", params: { ticketId: String(tk.id) } });
  };

  const newTicket = () => {
    dismissCall(call.call_id);
    void navigate({ to: "/agent/tickets/new", search: phoneTicketSearchForCall(call, single?.login) });
  };

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid={`call-card-${call.call_id}`}
      data-state={call.state}
      className={cn(
        "pointer-events-auto w-80 max-w-[90vw] rounded-lg border bg-surface p-3 text-left shadow-xl animate-route-in",
        call.state === "ringing" ? "border-channel-phone" : "border-hairline",
      )}
    >
      <div className="flex items-center gap-2">
        <PhoneIcon
          className={cn(
            "h-3.5 w-3.5 text-channel-phone",
            call.state === "ringing" && "motion-safe:animate-pulse",
          )}
        />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-channel-phone">
          {title}
        </span>
        {answered && (
          <span
            className="ml-auto font-mono text-[12px] tabular-nums text-muted"
            data-testid="call-card-timer"
          >
            {formatElapsed(callDurationSeconds(call, now))}
          </span>
        )}
      </div>
      <p className="mt-1 truncate text-[14px] font-medium text-ink" data-testid="call-card-who">
        {who}
      </p>
      {(single?.company || (single && number)) && (
        <p className="truncate text-[12px] text-muted">
          {[single?.company, number].filter(Boolean).join(" · ")}
        </p>
      )}

      {tickets.length > 0 && (
        <div className="mt-2 space-y-1" data-testid="call-card-tickets">
          {tickets.slice(0, 3).map((tk) => (
            <div key={tk.id} className="flex items-center gap-2 text-[12.5px]">
              <span className="min-w-0 flex-1 truncate text-ink" title={tk.title}>
                <span className="font-mono text-[11px] text-muted">{tk.tn}</span> {tk.title}
              </span>
              <span className="shrink-0 text-[11px] text-muted">{stateLabel(t, tk.state)}</span>
              <button
                type="button"
                data-testid={`call-card-log-${tk.id}`}
                onClick={() => logOnTicket(tk)}
                className="shrink-0 rounded border border-hairline px-1.5 py-0.5 text-[11.5px] text-ink hover:bg-surface-subtle"
              >
                {t("callPopup.logOnTicket")}
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          data-testid="call-card-new-ticket"
          onClick={newTicket}
          className="rounded bg-accent px-2 py-1 text-[12px] font-medium text-accent-ink hover:opacity-90"
        >
          {t("callPopup.newTicket")}
        </button>
        <button
          type="button"
          data-testid="call-card-dismiss"
          onClick={() => dismissCall(call.call_id)}
          className="ml-auto rounded px-2 py-1 text-[12px] text-muted hover:bg-surface-subtle hover:text-ink"
        >
          {ended ? t("callPopup.close") : t("callPopup.ignore")}
        </button>
      </div>
    </div>
  );
}

/**
 * CTI incoming-call popup (bottom right, one card per call, stacked): the PBX
 * reports ringing → answered → hangup via `POST /channels/phone/events`, the
 * backend pushes `call_event`s over SSE to the agents of the extension. The
 * caller is looked up through `/reference/caller` (same permission logic as
 * the phone ticket form). An ended call stays as "log this call?" for 15
 * minutes; after a reload the cards come back from `/phone/calls/active`.
 */
export function CallPopup() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const calls = useCalls();
  const connection = useConnectionStatus();
  const [now, setNow] = useState(() => Date.now());

  // Restore on mount and whenever the stream (re)connects — events sent while
  // it was down are only reflected in the server's call state.
  useEffect(() => {
    if (connection === "reconnecting") return undefined;
    const ctl = new AbortController();
    phoneApi
      .activeCalls(ctl.signal)
      .then((list) => restoreCalls(list))
      .catch(() => {
        // Redis down / no CTI — the popup simply stays empty
      });
    return () => ctl.abort();
  }, [connection]);

  const visible = visibleCalls(calls, user?.id, now);
  const ticking = calls.length > 0;
  useEffect(() => {
    if (!ticking) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [ticking]);

  if (visible.length === 0) return null;
  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-40 flex flex-col-reverse gap-2"
      data-testid="call-popup"
      aria-label={t("callPopup.region")}
    >
      {visible.map((call) => (
        <CallCard key={call.call_id} call={call} now={now} />
      ))}
    </div>
  );
}
