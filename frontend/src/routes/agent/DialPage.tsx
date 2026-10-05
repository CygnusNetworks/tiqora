import { useQuery } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { useOriginate } from "@/components/agent/phone/useOriginate";
import { phoneApi } from "@/lib/phoneApi";

/** Landing page of the report mail's "Zurückrufen" button: shows who will be
 * called and dials only on an explicit click (mail scanners fetch links). */
export function DialPage() {
  const { t } = useTranslation();
  const { number, ticket } = useSearch({ from: "/agent/dial" });
  const { enabled, status, dial } = useOriginate();
  const caller = useQuery({
    queryKey: ["reference", "caller", number],
    queryFn: () => phoneApi.callerLookup(number),
    enabled: number.length >= 5,
  });
  const customer = caller.data?.customers[0];
  const name = customer?.name ?? "";
  return (
    <div className="mx-auto max-w-md p-6" data-testid="dial-page">
      <h1 className="text-lg font-semibold">{t("phone.dialPageTitle")}</h1>
      <p className="mt-2 font-mono text-xl">{number || "–"}</p>
      {customer && (
        <p className="text-sm text-muted">
          {name}
          {customer.company ? ` (${customer.company})` : ""}
        </p>
      )}
      {ticket != null && (
        <p className="mt-1 text-sm">
          <Link to="/agent/tickets/$ticketId" params={{ ticketId: String(ticket) }}>
            {t("phone.dialPageTicket", { id: ticket })}
          </Link>
        </p>
      )}
      <div className="mt-4">
        {enabled ? (
          <Button
            data-testid="dial-page-call"
            disabled={!number || status.kind === "ringing"}
            onClick={() => void dial(number, { ticketId: ticket, name })}
          >
            {t("phone.dialPageCall")}
          </Button>
        ) : (
          <a className="text-accent underline" href={`tel:${number}`}>
            {t("phone.dialPageTel")}
          </a>
        )}
      </div>
      {status.kind === "ringing" && (
        <p className="mt-3 text-sm" data-testid="dial-page-status">
          {t("phone.dialRinging", { extension: status.extension })}
        </p>
      )}
      {status.kind === "error" && (
        <p className="mt-3 text-sm text-danger" role="alert">
          {status.message}
        </p>
      )}
    </div>
  );
}
