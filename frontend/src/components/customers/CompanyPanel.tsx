import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { BuildingIcon, DownloadIcon, PlusIcon } from "@/components/ui/icons";
import { api } from "@/lib/api";
import { CallAction, EmailTicketLink } from "./CustomerActions";
import { customerName, initialsOf } from "./customerFormat";

const CONTACTS_SHOWN = 200;

/**
 * A customer company: its contacts with the same quick actions as a single
 * customer, the company's tickets and the company vCard export.
 */
export function CompanyPanel({
  customerId,
  onSelectContact,
  onAddContact,
}: {
  customerId: string;
  onSelectContact: (login: string) => void;
  /** Shown only to agents who may edit customers. */
  onAddContact?: (company: { customer_id: string; name: string }) => void;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();

  const companyQ = useQuery({
    queryKey: ["customer-directory", "company", customerId],
    queryFn: ({ signal }) => api.getCustomerDirectoryCompany(customerId, signal),
    // 404 = a customer_id without a company row; nothing to retry.
    retry: false,
  });
  const contactsQ = useQuery({
    queryKey: ["customer-directory", "company-contacts", customerId],
    queryFn: ({ signal }) =>
      api.listCustomerDirectory(
        { customerId, valid: "valid", page: 1, pageSize: CONTACTS_SHOWN },
        signal,
      ),
  });
  const openQ = useQuery({
    queryKey: ["tickets", "customer-open", customerId],
    queryFn: ({ signal }) =>
      api.listTickets({ customer_id: customerId, state_type: "open", limit: 1 }, signal),
  });

  if (companyQ.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  // Customers may carry a customer_id without a customer_company row (private
  // customers): show the contacts anyway, named by the id.
  const co = companyQ.data;
  const name = co?.name || customerId;
  const address = co
    ? [co.street, [co.zip, co.city].filter(Boolean).join(" "), co.country]
        .map((part) => (part ?? "").trim())
        .filter(Boolean)
        .join(", ")
    : "";
  const contacts = contactsQ.data?.items ?? [];
  const contactTotal = contactsQ.data?.total ?? 0;
  const openCount = openQ.data?.total ?? 0;

  return (
    <div className="space-y-5" data-testid="company-panel">
      <header className="flex flex-wrap items-center gap-4">
        <span className="grid h-[52px] w-[52px] shrink-0 place-items-center rounded-xl border border-hairline bg-surface-subtle text-muted">
          <BuildingIcon className="text-[24px]" />
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-2xl font-semibold text-ink" data-testid="company-name">
            {name}
          </h1>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <span className="font-mono text-xs">{customerId}</span>
            {address && <span>{address}</span>}
            {co?.url && (
              <a href={co.url} target="_blank" rel="noreferrer" className="hover:underline">
                {co.url}
              </a>
            )}
          </div>
        </div>
        {onAddContact && user?.can_edit_customers && (
          <Button
            variant="primary"
            onClick={() => onAddContact({ customer_id: customerId, name })}
            data-testid="company-add-contact"
          >
            <PlusIcon className="text-[14px]" />
            {t("customerWorkbench.addContact")}
          </Button>
        )}
      </header>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-hairline pb-4 text-sm">
        <span className="text-muted">{t("customerWorkbench.contactCount", { count: contactTotal })}</span>
        <Link
          to="/agent/queues"
          search={{ customer_id: customerId, state_type: "all" }}
          className="text-accent hover:underline"
          data-testid="company-tickets-link"
        >
          {openCount > 0
            ? t("customerWorkbench.companyOpenTickets", { count: openCount })
            : t("customerWorkbench.companyTickets")}
        </Link>
        {contactTotal > 0 && (
          <a
            href={api.companyVcardsUrl(customerId)}
            download
            className="inline-flex items-center gap-1 text-accent hover:underline"
            data-testid="company-panel-vcards"
          >
            <DownloadIcon className="text-[13px]" />
            {t("customerWorkbench.companyVcards")}
          </a>
        )}
      </div>

      <section className="rounded-lg border border-hairline bg-surface" aria-labelledby="company-contacts-title">
        <h2 id="company-contacts-title" className="px-3.5 py-2.5 text-sm font-semibold text-ink">
          {t("customerWorkbench.contacts")}
        </h2>
        {contactsQ.isLoading ? (
          <div className="flex justify-center py-4">
            <Spinner className="h-4 w-4" />
          </div>
        ) : contacts.length === 0 ? (
          <p className="border-t border-hairline px-3.5 py-3 text-sm text-muted">
            {t("customerWorkbench.noContacts")}
          </p>
        ) : (
          <ul>
            {contacts.map((p) => {
              const pname = customerName(p);
              const number = (p.phone || p.mobile || "").trim();
              return (
                <li
                  key={p.login}
                  className="flex items-center gap-3 border-t border-hairline px-3.5 py-2"
                  data-testid={`company-contact-${p.login}`}
                >
                  <Avatar email={p.email} initials={initialsOf(pname)} size={30} />
                  <button
                    type="button"
                    onClick={() => onSelectContact(p.login)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <span className="block truncate font-medium text-ink hover:underline">{pname}</span>
                    <span className="block truncate text-xs text-muted">{p.email || p.login}</span>
                  </button>
                  <span className="hidden w-32 shrink-0 truncate text-xs tabular-nums text-muted md:block">
                    {number}
                  </span>
                  <EmailTicketLink login={p.login} compact />
                  {number && <CallAction customer={p} number={number} compact />}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
