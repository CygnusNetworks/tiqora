import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fieldOptionsApi } from "@/lib/fieldOptionsApi";
import { cmdbApi } from "@/lib/cmdbApi";
import { DEPLOYMENT_CATALOG, INCIDENT_CATALOG } from "@/components/cmdb/ciFilterModel";

export type FilterOption = { value: string; label: string };

type NamedRef = { id: number; name?: string; full_name?: string; login?: string };

const REFERENCE_PATH: Record<string, string> = {
  queue: "queues",
  state: "states",
  priority: "priorities",
  type: "types",
  service: "services",
  sla: "slas",
  user: "agents",
};

/** Report dimensions on CMDB data: the classes the viewer may read and the two state catalogs. */
const CMDB_CATALOG: Record<string, string> = {
  cmdb_deployment_state: DEPLOYMENT_CATALOG,
  cmdb_incident_state: INCIDENT_CATALOG,
};

/** Both CMDB endpoints are permission-filtered; a failure (no access, mode off) just leaves the field free-typed. */
async function loadCmdbOptions(source: string): Promise<FilterOption[]> {
  try {
    if (source === "cmdb_class") {
      return (await cmdbApi.classes()).map((c) => ({ value: String(c.id), label: c.name }));
    }
    return (await cmdbApi.catalog(CMDB_CATALOG[source])).map((c) => ({ value: String(c.id), label: c.name }));
  } catch {
    return [];
  }
}

async function loadOptions(source: string, signal?: AbortSignal): Promise<FilterOption[]> {
  if (source === "cmdb_class" || source in CMDB_CATALOG) return loadCmdbOptions(source);
  if (source.startsWith("df:")) {
    const field = `DynamicField_${source.slice(3)}`;
    const out = await fieldOptionsApi.fetch({ fields: [field] }, signal);
    return (out.options[field] ?? []).map((o) => ({ value: o.value, label: o.label }));
  }
  const path = REFERENCE_PATH[source];
  if (!path) return [];
  const rows = await api.request<NamedRef[]>("GET", `/api/v1/reference/${path}`, { signal });
  return rows.map((r) => ({
    value: String(r.id),
    label: r.full_name ?? r.name ?? r.login ?? String(r.id),
  }));
}

/**
 * Selectable values of a filter field (`"queue"`, `"user"`, `"df:<Name>"` ...), cached per
 * source for five minutes. `null` = free input, nothing is fetched. Values are strings; id
 * fields convert them to numbers in the value input.
 */
export function useFilterOptions(optionsSource: string | null): {
  options: FilterOption[];
  isLoading: boolean;
} {
  const q = useQuery({
    queryKey: ["filter-options", optionsSource],
    queryFn: ({ signal }) => loadOptions(optionsSource as string, signal),
    enabled: optionsSource !== null,
    staleTime: 5 * 60 * 1000,
  });
  return { options: q.data ?? [], isLoading: optionsSource !== null && q.isLoading };
}
