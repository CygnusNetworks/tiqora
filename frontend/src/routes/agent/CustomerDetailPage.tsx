import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { TicketTable, type SortKey } from "@/components/agent/TicketTable";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";

/**
 * Customer Information Centre (agent): contact master data + open/closed
 * counts and recent tickets via existing listTickets(customer_id=...).
 */
export function CustomerDetailPage() {
  const { t } = useTranslation();
  const { login: loginParam } = useParams({ from: "/agent/customers/$login" });
  const login = decodeURIComponent(loginParam ?? "");

  const customerQ = useQuery({
    queryKey: ["customers", login],
    queryFn: () => api.getCustomer(login),
    enabled: Boolean(login),
  });

  const customerId = customerQ.data?.customer_id;

  const openQ = useQuery({
    queryKey: ["tickets", "customer-open", customerId],
    queryFn: () =>
      api.listTickets({
        customer_id: customerId,
        state_type: "open",
        limit: 1,
        offset: 0,
      }),
    enabled: Boolean(customerId),
  });

  const closedQ = useQuery({
    queryKey: ["tickets", "customer-closed", customerId],
    queryFn: () =>
      api.listTickets({
        customer_id: customerId,
        state_type: "closed",
        limit: 1,
        offset: 0,
      }),
    enabled: Boolean(customerId),
  });

  const recentQ = useQuery({
    queryKey: ["tickets", "customer-recent", customerId],
    queryFn: () =>
      api.listTickets({
        customer_id: customerId,
        limit: 25,
        offset: 0,
        sort: "age",
        order: "desc",
      }),
    enabled: Boolean(customerId),
  });

  if (!login) {
    return <p className="p-6 text-sm text-danger">{t("customerCentre.invalid")}</p>;
  }

  if (customerQ.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }

  if (customerQ.isError || !customerQ.data) {
    return (
      <div className="p-6">
        <p className="text-sm text-danger">{t("customerCentre.loadError")}</p>
        <Link to="/agent/search" className="mt-2 inline-block text-sm text-accent hover:underline">
          {t("common.back")}
        </Link>
      </div>
    );
  }

  const c = customerQ.data;
  const openCount = openQ.data?.total ?? 0;
  const closedCount = closedQ.data?.total ?? 0;
  const sort: SortKey = "age";

  return (
    <div
      className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6"
      data-testid="customer-detail-page"
    >
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("customerCentre.title")}
        </p>
        <h1 className="font-display text-2xl font-semibold text-ink">
          {c.first_name} {c.last_name}
        </h1>
        <p className="mt-1 font-mono text-sm text-muted">{c.login}</p>
      </div>

      <div className="grid gap-4 rounded-lg border border-hairline bg-surface p-4 sm:grid-cols-2">
        <dl className="space-y-2 text-sm">
          <div>
            <dt className="text-xs text-muted">{t("customerCentre.email")}</dt>
            <dd>{c.email || "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">{t("customerCentre.phone")}</dt>
            <dd>{c.phone || "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">{t("customerCentre.customerId")}</dt>
            <dd className="font-mono">{c.customer_id}</dd>
          </div>
          {c.company_name && (
            <div>
              <dt className="text-xs text-muted">{t("customerCentre.company")}</dt>
              <dd>{c.company_name}</dd>
            </div>
          )}
        </dl>
        <div className="flex flex-wrap items-center gap-3">
          <Badge tone="accent" data-testid="customer-open-count">
            {t("customerCentre.openCount", { count: openCount })}
          </Badge>
          <Badge tone="muted" data-testid="customer-closed-count">
            {t("customerCentre.closedCount", { count: closedCount })}
          </Badge>
          {customerId && (
            <Link
              to="/agent/queues"
              search={{ customer_id: customerId, state_type: "open" }}
              className="text-sm font-medium text-accent hover:underline"
              data-testid="customer-all-tickets-link"
            >
              {t("customerCentre.allTickets")}
            </Link>
          )}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-ink">{t("customerCentre.recentTickets")}</h2>
        {recentQ.isLoading ? (
          <Spinner />
        ) : (
          <TicketTable
            items={recentQ.data?.items ?? []}
            total={recentQ.data?.total ?? 0}
            offset={0}
            limit={25}
            sort={sort}
            order="desc"
            onPageChange={() => {}}
            onSortChange={() => {}}
          />
        )}
      </div>
    </div>
  );
}
