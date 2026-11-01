import { useQuery } from "@tanstack/react-query";
import { approvalsApi, approvalsKeys } from "@/lib/approvalsApi";

/** The module switch `approvals.enabled`; an open page sees a change within a minute, also idle. */
export function useApprovalsStatus() {
  return useQuery({
    queryKey: approvalsKeys.status(),
    queryFn: ({ signal }) => approvalsApi.status(signal),
    retry: false,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}

/** Whether to show approvals: not before the answer, and a failed status keeps them (the routes still refuse). */
export function useApprovalsEnabled(): boolean {
  const q = useApprovalsStatus();
  return q.data?.enabled ?? q.isError;
}
