import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { FieldOption } from "@/components/fields/types";
import { api } from "@/lib/api";
import type { GroupPermission } from "@/lib/identityApi";

export const GROUP_PERMISSIONS: GroupPermission[] = ["ro", "move_into", "create", "note", "owner", "priority", "rw"];

/** Roles and groups an identity provider can hand out (valid ones, first 500 like the role/group pages). */
export function useDirectory() {
  const rolesQ = useQuery({
    queryKey: ["admin", "directory", "roles"],
    queryFn: ({ signal }) => api.adminRoles.list({ valid: "valid", pageSize: 500 }, signal),
    staleTime: 60_000,
  });
  const groupsQ = useQuery({
    queryKey: ["admin", "directory", "groups"],
    queryFn: ({ signal }) => api.adminGroups.list({ valid: "valid", pageSize: 500 }, signal),
    staleTime: 60_000,
  });
  const roleOptions = useMemo<FieldOption[]>(
    () => (rolesQ.data?.items ?? []).map((r) => ({ value: String(r.id), label: r.name })),
    [rolesQ.data],
  );
  const groupOptions = useMemo<FieldOption[]>(
    () => (groupsQ.data?.items ?? []).map((g) => ({ value: String(g.id), label: g.name })),
    [groupsQ.data],
  );
  return { roleOptions, groupOptions, loading: rolesQ.isLoading || groupsQ.isLoading };
}
