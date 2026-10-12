import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Spinner } from "@/components/ui/Spinner";
import { cmdbApi } from "@/lib/cmdbApi";

/** What a CI in trouble would touch: dependent CIs, services, customers and open tickets. */
export function AffectedPanel({ ciId }: { ciId: number }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["cmdb", "ci", ciId, "affected"], queryFn: () => cmdbApi.affected(ciId) });
  if (q.isError) {
    return (
      <p role="alert" className="text-sm text-danger">
        {t("cmdb.affected.loadFailed")}
      </p>
    );
  }
  if (q.isPending) return <Spinner />;
  const a = q.data;
  const counts: Array<[string, number]> = [
    ["ci", a.ci_count],
    ["services", a.service_count],
    ["customers", a.customer_count],
    ["tickets", a.open_ticket_count],
  ];
  const nothing = counts.every(([, n]) => n === 0);
  return (
    <section aria-labelledby="ci-affected-title" className="space-y-3" data-testid="ci-affected">
      <h3 id="ci-affected-title" className="text-sm font-semibold text-ink">
        {t("cmdb.affected.title")}
      </h3>
      {nothing ? (
        <p className="text-sm text-muted" data-testid="ci-affected-empty">
          {t("cmdb.affected.none")}
        </p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {counts.map(([k, n]) => (
              <div key={k} className="rounded-md border border-hairline bg-surface px-3 py-2">
                <dt className="text-xs text-muted">{t(`cmdb.affected.counts.${k}`)}</dt>
                <dd className="text-lg font-semibold text-ink" data-testid={`ci-affected-count-${k}`}>
                  {n}
                </dd>
              </div>
            ))}
          </dl>
          {a.services.length > 0 && (
            <div>
              <h4 className="mb-1 text-xs font-medium text-muted">{t("cmdb.affected.services")}</h4>
              <ul className="list-inside list-disc text-sm text-ink" data-testid="ci-affected-services">
                {a.services.map((s) => (
                  <li key={s.id}>{s.label}</li>
                ))}
              </ul>
            </div>
          )}
          {a.customers.length > 0 && (
            <div>
              <h4 className="mb-1 text-xs font-medium text-muted">{t("cmdb.affected.customers")}</h4>
              <ul className="list-inside list-disc text-sm text-ink" data-testid="ci-affected-customers">
                {a.customers.map((c) => (
                  <li key={c.id}>{c.label}</li>
                ))}
              </ul>
            </div>
          )}
          {a.open_tickets.length > 0 && (
            <div>
              <h4 className="mb-1 text-xs font-medium text-muted">{t("cmdb.affected.tickets")}</h4>
              <ul className="divide-y divide-hairline rounded-md border border-hairline text-sm" data-testid="ci-affected-tickets">
                {a.open_tickets.map((tk) => (
                  <li key={tk.ticket_id} className="flex flex-wrap items-baseline gap-x-3 px-3 py-1.5">
                    <Link to="/agent/tickets/$ticketId" params={{ ticketId: String(tk.ticket_id) }} className="font-medium text-accent hover:underline">
                      {tk.number}
                    </Link>
                    <span className="min-w-0 flex-1 truncate text-ink">{tk.title}</span>
                    <span className="text-xs text-muted">{tk.state_name}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {a.truncated && (
        <p className="text-xs text-muted" data-testid="ci-affected-truncated">
          {t("cmdb.affected.truncated")}
        </p>
      )}
    </section>
  );
}
