import type { ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useOriginate } from "@/components/agent/phone/useOriginate";
import { MailIcon, MobileIcon, PhoneIcon, PhoneOutgoingIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { actionClass, iconActionClass } from "./customerFormat";
import { dialHref } from "@/lib/phoneCall";

type Customer = {
  login: string;
  first_name?: string | null;
  last_name?: string | null;
};

const displayName = (c: Customer) =>
  [c.first_name, c.last_name].filter(Boolean).join(" ").trim() || c.login;

/** "New e-mail ticket" for this customer (the new-ticket form, prefilled). */
export function EmailTicketLink({
  login,
  compact,
  primary,
}: {
  login: string;
  compact?: boolean;
  primary?: boolean;
}) {
  const { t } = useTranslation();
  const label = t("customerWorkbench.emailTicket");
  return (
    <Link
      to="/agent/tickets/new"
      search={{ type: "email", customer: login }}
      className={compact ? iconActionClass : actionClass(primary)}
      aria-label={compact ? label : undefined}
      title={compact ? label : undefined}
      data-testid={`customer-email-ticket-${login}`}
    >
      <MailIcon className="text-[15px]" />
      {!compact && label}
    </Link>
  );
}

/** "Log a call from them": an inbound phone ticket, no dialling. */
export function PhoneTicketLink({ login }: { login: string }) {
  const { t } = useTranslation();
  return (
    <Link
      to="/agent/tickets/new"
      search={{ type: "phone", direction: "inbound", customer: login }}
      className={actionClass()}
      data-testid={`customer-phone-ticket-${login}`}
    >
      <PhoneIcon className="text-[15px] text-channel-phone" />
      {t("customerWorkbench.phoneTicket")}
    </Link>
  );
}

/**
 * Call a number. With click-to-dial configured the agent's desk phone rings
 * first; once the PBX took the call the outbound phone ticket opens (the same
 * flow as `DialLink`). Without it, a `tel:`/`sip:` link that opens the form
 * right away. Ctrl/Cmd-click always follows the plain link.
 */
export function CallAction({
  customer,
  number,
  kind = "phone",
  compact,
}: {
  customer: Customer;
  number: string;
  kind?: "phone" | "mobile";
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { enabled, scheme, status, dial } = useOriginate();
  const openForm = () =>
    void navigate({
      to: "/agent/tickets/new",
      search: { type: "phone", direction: "outbound", customer: customer.login, number },
    });
  const label = t("customerWorkbench.callNumber", { number });
  const icon: ReactNode =
    kind === "mobile" ? (
      <MobileIcon className="text-[15px] text-green" />
    ) : (
      <PhoneOutgoingIcon className="text-[15px] text-green" />
    );
  return (
    <span className="inline-flex flex-col">
      <a
        href={dialHref(number, scheme)}
        onClick={(e) => {
          if (enabled && !e.metaKey && !e.ctrlKey) {
            e.preventDefault();
            void dial(number, { name: displayName(customer) }).then((ok) => {
              if (ok) openForm();
            });
            return;
          }
          openForm();
        }}
        className={compact ? iconActionClass : cn(actionClass(), "tabular-nums")}
        aria-label={compact ? label : undefined}
        title={compact ? label : t("customerWorkbench.callHint")}
        data-testid={`customer-call-${kind}-${customer.login}`}
      >
        {icon}
        {!compact && number}
      </a>
      {!compact && status.kind === "ringing" && (
        <span className="mt-0.5 text-xs text-muted">
          {t("phone.dialRinging", { extension: status.extension })}
        </span>
      )}
      {status.kind === "error" && (
        <span className="mt-0.5 text-xs text-danger" role="alert">
          {status.message}
        </span>
      )}
    </span>
  );
}
