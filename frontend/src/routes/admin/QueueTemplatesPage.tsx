import {
  AssignmentEditor,
  type AssignmentConfig,
} from "@/components/admin/AssignmentEditor";
import { api, type QueueOut, type StandardTemplateOut } from "@/lib/api";

/**
 * Queue↔Templates assignment editor (bidirectional master-detail).
 */
export function QueueTemplatesPage() {
  const config: AssignmentConfig<QueueOut, StandardTemplateOut> = {
    testId: "admin-queue-templates-page",
    titleKey: "admin.queueTemplates.title",
    subtitleKey: "admin.queueTemplates.subtitle",
    sideA: {
      key: "queues",
      labelKey: "admin.queueTemplates.queue",
      loadItems: async (signal) => {
        const page = await api.adminQueues.list({ valid: "all", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (q) => q.id,
      getLabel: (q) => q.name,
      isValid: (q) => q.valid_id === 1,
    },
    sideB: {
      key: "templates",
      labelKey: "admin.queueTemplates.templates",
      loadItems: async (signal) => {
        const page = await api.adminTemplates.list({ valid: "all", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (t) => t.id,
      getLabel: (t) => t.name,
      getSubLabel: (t) => t.template_type ?? undefined,
      isValid: (t) => t.valid_id === 1,
    },
    loadAssignedB: (qId, signal) => api.listQueueTemplates(qId as number, signal),
    loadAssignedA: (tId, signal) => api.listTemplateQueues(tId as number, signal),
    loadCounts: (dir, signal) =>
      dir === "a"
        ? api.listQueueAssignmentCounts("templates", signal)
        : api.listTemplateAssignmentCounts("queues", signal),
    assign: (qId, tId) => api.assignQueueTemplate(qId as number, tId as number),
    revoke: (qId, tId) => api.revokeQueueTemplate(qId as number, tId as number),
  };

  return <AssignmentEditor config={config} />;
}
