import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { useCustomerCryptoKeys } from "@/components/agent/customerCryptoKeysQuery";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { StateChip } from "@/components/ui/StatusChip";
import { Spinner } from "@/components/ui/Spinner";
import { BuildingIcon, DownloadIcon, PencilIcon, StarIcon } from "@/components/ui/icons";
import { api, type CustomerUserOut } from "@/lib/api";
import { cn } from "@/lib/cn";
import { humanDuration } from "@/lib/status";
import { CallAction, EmailTicketLink, PhoneTicketLink } from "./CustomerActions";
import {
  CustomerKeysAddLink,
  CustomerKeysDialog,
  CustomerKeysSummary,
  EncryptedReachChip,
} from "./CustomerKeys";
import { customerName, initialsOf } from "./customerFormat";
import { useCustomerShortlist, useFavoriteToggle } from "./favorites";

const TICKETS_SHOWN = 8;

/**
 * One customer user with what an agent does next front and centre: start an
 * e-mail ticket, log a call, dial. Below: their own tickets (not the whole
 * company's), contact data and the vCard / key extras.
 */
export function CustomerPanel({
  login,
  onEdit,
  onOpenCompany,
}: {
  login: string;
  /** Shown only to agents who may edit customers. */
  onEdit?: (customer: CustomerUserOut) => void;
  /** Company name click; default links to the company view of the "Kunden" page. */
  onOpenCompany?: (customerId: string) => void;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [keysOpen, setKeysOpen] = useState(false);

  const customerQ = useQuery({
    queryKey: ["customers", login],
    queryFn: ({ signal }) => api.getCustomer(login, signal),
    enabled: Boolean(login),
  });
  const ticketsQ = useQuery({
    queryKey: ["tickets", "customer-user", login],
    queryFn: ({ signal }) =>
      api.listTickets(
        { customer_user_id: login, limit: TICKETS_SHOWN, sort: "changed", order: "desc" },
        signal,
      ),
    enabled: Boolean(login),
  });
  const openQ = useQuery({
    queryKey: ["tickets", "customer-user-open", login],
    queryFn: ({ signal }) =>
      api.listTickets({ customer_user_id: login, state_type: "open", limit: 1 }, signal),
    enabled: Boolean(login),
  });
  const keysQ = useCustomerCryptoKeys(login);
  // Favorites live on the "Kunden" page, so starring needs its grant.
  const canStar = Boolean(user?.can_use_customer_directory);
  const shortlistQ = useCustomerShortlist(canStar);
  const toggleFavorite = useFavoriteToggle();

  if (customerQ.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (customerQ.isError || !customerQ.data) {
    return (
      <p className="p-6 text-sm text-danger" data-testid="customer-panel-error">
        {t("customerCentre.loadError")}
      </p>
    );
  }

  const c = customerQ.data;
  const name = customerName(c);
  const phone = c.phone?.trim();
  const mobile = c.mobile?.trim();
  const address = [c.street, [c.zip, c.city].filter(Boolean).join(" "), c.country]
    .map((part) => (part ?? "").trim())
    .filter(Boolean)
    .join(", ");
  const tickets = ticketsQ.data?.items ?? [];
  const total = ticketsQ.data?.total ?? 0;
  const openCount = openQ.data?.total ?? 0;
  const invalid = c.valid_id != null && c.valid_id !== 1;
  const favorite = Boolean(shortlistQ.data?.favorites.some((f) => f.login === c.login));

  const companyLabel = c.company_name || c.customer_id;
  const companyLink = onOpenCompany ? (
    <button
      type="button"
      onClick={() => onOpenCompany(c.customer_id)}
      className="inline-flex items-center gap-1.5 font-medium text-ink hover:underline"
      data-testid="customer-panel-company"
    >
      <BuildingIcon className="text-[15px] text-muted" />
      {companyLabel}
    </button>
  ) : !user?.can_use_customer_directory ? (
    <span className="inline-flex items-center gap-1.5 font-medium text-ink" data-testid="customer-panel-company">
      <BuildingIcon className="text-[15px] text-muted" />
      {companyLabel}
    </span>
  ) : (
    <Link
      to="/agent/customers"
      search={{ tab: "companies", company: c.customer_id }}
      className="inline-flex items-center gap-1.5 font-medium text-ink hover:underline"
      data-testid="customer-panel-company"
    >
      <BuildingIcon className="text-[15px] text-muted" />
      {companyLabel}
    </Link>
  );

  return (
    <div className="space-y-5" data-testid="customer-panel">
      <header className="flex flex-wrap items-center gap-4">
        <Avatar email={c.email} initials={initialsOf(name)} size={52} />
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-2xl font-semibold text-ink" data-testid="customer-name">
            {name}
            {invalid && (
              <span className="ml-2 align-middle text-xs font-normal text-danger">
                {t("customerWorkbench.invalid")}
              </span>
            )}
          </h1>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            {c.title && <span>{c.title}</span>}
            {companyLink}
            <span className="font-mono text-xs">{c.customer_id}</span>
            <EncryptedReachChip data={keysQ.data} />
          </div>
        </div>
        {canStar && (
          <Button
            variant="ghost"
            onClick={() => toggleFavorite.mutate({ customer: c, favorite: !favorite })}
            aria-pressed={favorite}
            disabled={!shortlistQ.data}
            title={favorite ? t("customerWorkbench.favoriteRemove") : t("customerWorkbench.favoriteAdd")}
            data-testid="customer-favorite"
          >
            <StarIcon
              className={cn("text-[15px]", favorite && "text-warn")}
              fill={favorite ? "currentColor" : "none"}
            />
            {t("customerWorkbench.favorite")}
          </Button>
        )}
        {onEdit && user?.can_edit_customers && (
          <Button variant="ghost" onClick={() => onEdit(c)} data-testid="customer-edit">
            <PencilIcon className="text-[14px]" />
            {t("customerWorkbench.edit")}
          </Button>
        )}
      </header>

      <div
        className="flex flex-wrap items-start gap-2 border-b border-hairline pb-5"
        data-testid="customer-actions"
      >
        <EmailTicketLink login={c.login} primary />
        <PhoneTicketLink login={c.login} />
        {phone && <CallAction customer={c} number={phone} />}
        {mobile && <CallAction customer={c} number={mobile} kind="mobile" />}
      </div>

      <div className="flex flex-wrap items-start gap-6">
        <section
          className="min-w-0 flex-[999_1_340px] rounded-lg border border-hairline bg-surface"
          aria-labelledby="customer-tickets-title"
        >
          <div className="flex items-center gap-2 px-3.5 py-2.5">
            <h2 id="customer-tickets-title" className="text-sm font-semibold text-ink">
              {t("customerWorkbench.tickets")}
            </h2>
            {openCount > 0 && (
              <span className="text-xs font-medium text-escalation" data-testid="customer-open-count">
                {t("customerWorkbench.openCount", { count: openCount })}
              </span>
            )}
            {total > TICKETS_SHOWN && (
              <Link
                to="/agent/search"
                search={{ customer_id: c.customer_id, customer_label: companyLabel }}
                className="ml-auto text-xs text-accent hover:underline"
              >
                {t("customerWorkbench.companyTickets")}
              </Link>
            )}
          </div>
          {ticketsQ.isLoading ? (
            <div className="flex justify-center py-4">
              <Spinner className="h-4 w-4" />
            </div>
          ) : tickets.length === 0 ? (
            <p className="border-t border-hairline px-3.5 py-3 text-sm text-muted">
              {t("customerWorkbench.noTickets")}
            </p>
          ) : (
            <ul>
              {tickets.map((tk) => (
                <li key={tk.id} className="border-t border-hairline">
                  <Link
                    to="/agent/tickets/$ticketId"
                    params={{ ticketId: String(tk.id) }}
                    className="flex items-center gap-3 px-3.5 py-2 hover:bg-surface-subtle"
                    data-testid={`customer-ticket-${tk.id}`}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium text-ink">{tk.title}</span>
                      <span className="block font-mono text-[11px] text-muted">{tk.tn}</span>
                    </span>
                    <StateChip state={tk.state} />
                    {tk.age_seconds != null && (
                      <span className="w-20 shrink-0 text-right text-xs text-muted">
                        {humanDuration(t, tk.age_seconds)}
                      </span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <aside className="min-w-0 flex-[1_1_220px] space-y-3 text-sm">
          <dl className="space-y-2.5">
            <div>
              <dt className="text-xs text-muted">{t("customerCentre.email")}</dt>
              <dd className="break-all">
                {c.email ? (
                  <a href={`mailto:${c.email}`} className="text-ink hover:underline">
                    {c.email}
                  </a>
                ) : (
                  "—"
                )}
              </dd>
            </div>
            <CustomerKeysSummary data={keysQ.data} onOpen={() => setKeysOpen(true)} />
            {address && (
              <div>
                <dt className="text-xs text-muted">{t("customerWorkbench.address")}</dt>
                <dd>{address}</dd>
              </div>
            )}
            <div>
              <dt className="text-xs text-muted">{t("customerWorkbench.login")}</dt>
              <dd className="break-all font-mono text-xs">{c.login}</dd>
            </div>
            {c.comments && (
              <div>
                <dt className="text-xs text-muted">{t("customerWorkbench.comments")}</dt>
                <dd className="whitespace-pre-line">{c.comments}</dd>
              </div>
            )}
          </dl>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            <a
              href={api.customerVcardUrl(c.login)}
              download
              className="inline-flex items-center gap-1 text-accent hover:underline"
              data-testid="customer-vcard"
            >
              <DownloadIcon className="text-[13px]" />
              {t("customerWorkbench.vcard")}
            </a>
            {/* A company export is a list export: needs the customer-directory grant. */}
            {c.company_name && user?.can_use_customer_directory && (
              <a
                href={api.companyVcardsUrl(c.customer_id)}
                download
                className="inline-flex items-center gap-1 text-accent hover:underline"
                data-testid="company-vcards"
              >
                <DownloadIcon className="text-[13px]" />
                {t("customerWorkbench.companyVcards")}
              </a>
            )}
            <CustomerKeysAddLink data={keysQ.data} onOpen={() => setKeysOpen(true)} />
          </div>
        </aside>
      </div>

      <CustomerKeysDialog login={c.login} open={keysOpen} onClose={() => setKeysOpen(false)} />
    </div>
  );
}
