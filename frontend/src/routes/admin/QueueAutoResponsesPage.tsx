import {
  AssignmentEditor,
  type AssignmentConfig,
} from "@/components/admin/AssignmentEditor";
import { api, type AutoResponseOut, type QueueOut } from "@/lib/api";

/**
 * Queue↔Auto-responses assignment editor (bidirectional master-detail).
 */
export function QueueAutoResponsesPage() {
  const config: AssignmentConfig<QueueOut, AutoResponseOut> = {
    testId: "admin-queue-auto-responses-page",
    titleKey: "admin.queueAutoResponses.title",
    subtitleKey: "admin.queueAutoResponses.subtitle",
    sideA: {
      key: "queues",
      labelKey: "admin.queueAutoResponses.queue",
      loadItems: async (signal) => {
        const page = await api.adminQueues.list({ valid: "valid", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (q) => q.id,
      getLabel: (q) => q.name,
    },
    sideB: {
      key: "auto-responses",
      labelKey: "admin.queueAutoResponses.autoResponses",
      loadItems: async (signal) => {
        const page = await api.adminAutoResponses.list(
          { valid: "valid", pageSize: 500 },
          signal,
        );
        return page.items;
      },
      getId: (ar) => ar.id,
      getLabel: (ar) => ar.name,
      getSubLabel: (ar) => String(ar.type_id),
    },
    loadAssignedB: (qId, signal) => api.listQueueAutoResponses(qId as number, signal),
    loadAssignedA: (arId, signal) => api.listAutoResponseQueues(arId as number, signal),
    loadCounts: (dir, signal) =>
      dir === "a"
        ? api.listQueueAssignmentCounts("auto-responses", signal)
        : api.listAutoResponseAssignmentCounts("queues", signal),
    assign: (qId, arId) => api.assignQueueAutoResponse(qId as number, arId as number),
    revoke: (qId, arId) => api.revokeQueueAutoResponse(qId as number, arId as number),
  };

  return <AssignmentEditor config={config} />;
}
