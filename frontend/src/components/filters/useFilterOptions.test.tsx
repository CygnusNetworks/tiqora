import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { cmdbApi } from "@/lib/cmdbApi";
import { useFilterOptions } from "./useFilterOptions";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("useFilterOptions CMDB sources", () => {
  it("lists the readable CI classes by id and name", async () => {
    vi.spyOn(cmdbApi, "classes").mockResolvedValue([
      { id: 4, name: "Server" },
      { id: 7, name: "Standort" },
    ] as never);
    const { result } = renderHook(() => useFilterOptions("cmdb_class"), { wrapper });
    await waitFor(() => expect(result.current.options).toHaveLength(2));
    expect(result.current.options).toEqual([
      { value: "4", label: "Server" },
      { value: "7", label: "Standort" },
    ]);
  });

  it.each([
    ["cmdb_deployment_state", "ITSM::ConfigItem::DeploymentState"],
    ["cmdb_incident_state", "ITSM::Core::IncidentState"],
  ])("%s loads the catalog class %s", async (source, catalogClass) => {
    const catalog = vi.spyOn(cmdbApi, "catalog").mockResolvedValue([{ id: 12, name: "Warnung" }] as never);
    const { result } = renderHook(() => useFilterOptions(source), { wrapper });
    await waitFor(() => expect(result.current.options).toEqual([{ value: "12", label: "Warnung" }]));
    expect(catalog).toHaveBeenCalledWith(catalogClass);
  });

  it("answers an empty list when the endpoint fails", async () => {
    vi.spyOn(cmdbApi, "classes").mockRejectedValue(new Error("403"));
    const { result } = renderHook(() => useFilterOptions("cmdb_class"), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.options).toEqual([]);
  });
});
