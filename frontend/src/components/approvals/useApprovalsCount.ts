import { useQuery } from "@tanstack/react-query";
import { approvalsApi, approvalsKeys } from "@/lib/approvalsApi";

/** Pending-approval count of the sidebar badge: pushed by SSE `approval_changed`, polled every 120 s otherwise. */
export function useApprovalsCount() {
  return useQuery({
    queryKey: approvalsKeys.count(),
    queryFn: ({ signal }) => approvalsApi.inboxCount(signal),
    refetchInterval: 120_000,
    retry: false,
  });
}
