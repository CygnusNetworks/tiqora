import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { dismissCall, phoneTicketSearchForCall } from "@/components/agent/phone/callTicket";
import { PhoneIcon, PlusIcon } from "@/components/ui/icons";
import { useCalls, visibleCalls } from "@/lib/callPopup";
import { phoneApi } from "@/lib/phoneApi";

/**
 * The current call above the customer workbench: who is calling (caller
 * lookup, as in the CTI popup), one click to log it as a phone ticket, one
 * to open the caller here. Only calls still ringing or running; the popup
 * keeps handling ended ones.
 */
export function CustomerCallStrip({ onShowCustomer }: { onShowCustomer: (login: string) => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const call = visibleCalls(useCalls(), user?.id).find((c) => c.state !== "ended");
  const number = call?.number.trim() ?? "";
  const lookupQ = useQuery({
    queryKey: ["reference", "caller", number],
    queryFn: ({ signal }) => phoneApi.callerLookup(number, signal),
    enabled: number.replace(/\D/g, "").length >= 5,
    staleTime: 60 * 1000,
  });
  if (!call) return null;

  const customers = lookupQ.data?.customers ?? [];
  const single = customers.length === 1 ? customers[0] : undefined;
  const openTickets = lookupQ.data?.open_tickets?.length ?? 0;
  const outbound = call.direction === "outbound";
  const heading =
    call.state === "answered"
      ? t("callPopup.answered")
      : outbound
        ? t("callPopup.outgoing")
        : t("callPopup.ringing");
  const who = single
    ? [single.name || single.login, single.company].filter(Boolean).join(", ")
    : customers.length > 1
      ? t("callPopup.severalCustomers", { count: customers.length })
      : t("callPopup.unknownNumber");

  return (
    <section
      aria-label={heading}
      aria-live="polite"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-green/25 bg-green/10 px-5 py-2.5"
      data-testid="customer-call-strip"
    >
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-green text-bg">
        <PhoneIcon
          className={call.state === "ringing" ? "text-[17px] motion-safe:animate-pulse" : "text-[17px]"}
        />
      </span>
      <div className="min-w-0 flex-[1_1_16rem]">
        <p className="text-sm font-semibold text-ink">
          {heading}
          <span className="ml-2 font-normal tabular-nums text-green">{number}</span>
        </p>
        <p className="truncate text-xs text-muted" data-testid="customer-call-strip-who">
          {who}
          {openTickets > 0 && `, ${t("customerWorkbench.openCount", { count: openTickets })}`}
        </p>
      </div>
      <button
        type="button"
        onClick={() => {
          dismissCall(call.call_id);
          void navigate({
            to: "/agent/tickets/new",
            search: phoneTicketSearchForCall(call, single?.login),
          });
        }}
        className="inline-flex items-center gap-1.5 rounded-md bg-green px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-green"
        data-testid="customer-call-strip-ticket"
      >
        <PlusIcon className="text-[14px]" />
        {t("customerWorkbench.logCall")}
      </button>
      {single && (
        <button
          type="button"
          onClick={() => onShowCustomer(single.login)}
          className="rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          data-testid="customer-call-strip-show"
        >
          {t("customerWorkbench.showCaller")}
        </button>
      )}
    </section>
  );
}
