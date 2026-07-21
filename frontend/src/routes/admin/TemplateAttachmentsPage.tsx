import {
  AssignmentEditor,
  type AssignmentConfig,
} from "@/components/admin/AssignmentEditor";
import { api, type StandardAttachmentOut, type StandardTemplateOut } from "@/lib/api";

/**
 * Template↔Attachments assignment editor (bidirectional master-detail).
 *
 * Backend only exposes replace (full set) for writes — assign/revoke are
 * implemented as read-current + replace so the shared editor can toggle
 * immediately like the other relation pages.
 */
async function assignTemplateAttachment(templateId: number, attachmentId: number) {
  const current = await api.listTemplateAttachments(templateId);
  const ids = new Set(current.map((a) => a.id));
  ids.add(attachmentId);
  await api.replaceTemplateAttachments(templateId, {
    attachment_ids: Array.from(ids),
  });
}

async function revokeTemplateAttachment(templateId: number, attachmentId: number) {
  const current = await api.listTemplateAttachments(templateId);
  const ids = current.map((a) => a.id).filter((id) => id !== attachmentId);
  await api.replaceTemplateAttachments(templateId, { attachment_ids: ids });
}

export function TemplateAttachmentsPage() {
  const config: AssignmentConfig<StandardTemplateOut, StandardAttachmentOut> = {
    testId: "admin-template-attachments-page",
    titleKey: "admin.templateAttachments.title",
    subtitleKey: "admin.templateAttachments.subtitle",
    sideA: {
      key: "templates",
      labelKey: "admin.templateAttachments.template",
      loadItems: async (signal) => {
        const page = await api.adminTemplates.list({ valid: "valid", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (t) => t.id,
      getLabel: (t) => t.name,
      getSubLabel: (t) => t.template_type ?? undefined,
    },
    sideB: {
      key: "attachments",
      labelKey: "admin.templateAttachments.attachments",
      loadItems: async (signal) => {
        const page = await api.adminAttachments.list(
          { valid: "valid", pageSize: 500 },
          signal,
        );
        return page.items;
      },
      getId: (a) => a.id,
      getLabel: (a) => a.name,
      getSubLabel: (a) => a.filename,
    },
    loadAssignedB: async (templateId, signal) => {
      const refs = await api.listTemplateAttachments(templateId as number, signal);
      // AttachmentRefOut is a slim row; checklist only needs id/name/filename.
      return refs as unknown as StandardAttachmentOut[];
    },
    loadAssignedA: (attachmentId, signal) =>
      api.listAttachmentTemplates(attachmentId as number, signal),
    loadCounts: (dir, signal) =>
      dir === "a"
        ? api.listTemplateAssignmentCounts("attachments", signal)
        : api.listAttachmentAssignmentCounts("templates", signal),
    assign: (templateId, attachmentId) =>
      assignTemplateAttachment(templateId as number, attachmentId as number),
    revoke: (templateId, attachmentId) =>
      revokeTemplateAttachment(templateId as number, attachmentId as number),
  };

  return <AssignmentEditor config={config} />;
}
