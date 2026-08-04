import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { TicketTable, type SortKey } from "@/components/agent/TicketTable";
import { SelectField } from "@/components/ui/SelectField";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import { Spinner } from "@/components/ui/Spinner";

export type ServiceViewSearch = {
  service_id?: number;
  state_type?: "open" | "new" | "pending" | "closed" | "all";
  offset?: number;
};

/**
 * Service-centric ticket list: pick a service, list tickets with that
 * service_id via the standard list API (same filters as QueuesPage presets).
 */
export function ServiceViewPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/agent/services" });
  const search = useSearch({ from: "/agent/services" }) as ServiceViewSearch;
  const serviceId = search.service_id;
  const stateType = search.state_type ?? "open";
  const offset = search.offset ?? 0;
  const limit = 50;
  const sort: SortKey = "age";
  const order: "asc" | "desc" = "desc";

  const setSearch = (patch: Partial<ServiceViewSearch>) => {
    void navigate({
      search: (prev) => ({ ...(prev as ServiceViewSearch), ...patch }),
      replace: true,
    });
  };

  const servicesQ = useQuery({
    queryKey: ["reference", "services"],
    queryFn: () => api.listReferenceServices(),
  });

  const ticketsQ = useQuery({
    queryKey: ["tickets", "by-service", { serviceId, stateType, offset, limit }],
    queryFn: () =>
      api.listTickets({
        service_id: serviceId,
        state_type: stateType === "all" ? undefined : stateType,
        offset,
        limit,
        sort,
        order,
      }),
    enabled: serviceId != null,
  });

  const serviceItems: SelectMenuItem<number>[] = (servicesQ.data ?? []).map((s) => ({
    value: s.id,
    label: s.name,
  }));

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6" data-testid="service-view-page">
      <div>
        <h1 className="font-display text-xl font-bold tracking-tight text-ink">
          {t("views.service")}
        </h1>
        <p className="mt-0.5 text-[12.5px] text-muted">{t("views.serviceHint")}</p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-xs font-medium text-muted">{t("ticket.service")}</label>
        <div className="min-w-[16rem]">
          <SelectField
            items={serviceItems}
            value={serviceId ?? null}
            onChange={(id) => setSearch({ service_id: id, offset: 0 })}
            placeholder={t("views.pickService")}
            testId="service-view-picker"
          />
        </div>
      </div>

      {serviceId == null ? (
        <p className="rounded-lg border border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("views.pickService")}
        </p>
      ) : ticketsQ.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : (
        <TicketTable
          items={ticketsQ.data?.items ?? []}
          total={ticketsQ.data?.total ?? 0}
          offset={offset}
          limit={limit}
          sort={sort}
          order={order}
          isLoading={ticketsQ.isLoading}
          onPageChange={(nextOffset) => setSearch({ offset: nextOffset })}
          onSortChange={() => {
            /* fixed sort for this view */
          }}
        />
      )}
    </div>
  );
}
