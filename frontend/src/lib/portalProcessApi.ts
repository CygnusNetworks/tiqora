/** Customer-portal process endpoints (`/api/portal/process`), same shape as `processApi` minus ACL actions. */
import { portalApi } from "./portalApi";
import type {
  ActivityDialogSubmitOut,
  NewProcessTicketIn,
  NewProcessTicketOut,
  ProcessDialogFormData,
  ProcessSummary,
  TicketProcessState,
} from "./processApi";

const enc = encodeURIComponent;
const ticketQuery = (opts?: { ticketId?: number }) =>
  opts?.ticketId === undefined ? undefined : { ticket_id: opts.ticketId };

export const portalProcessApi = {
  listStartable(opts?: { ticketId?: number }, signal?: AbortSignal) {
    return portalApi.request<ProcessSummary[]>("GET", "/api/portal/process/", { query: ticketQuery(opts), signal });
  },
  startForm(processEntityId: string, opts?: { ticketId?: number }, signal?: AbortSignal) {
    return portalApi.request<ProcessDialogFormData>(
      "GET",
      `/api/portal/process/${enc(processEntityId)}/start-form`,
      { query: ticketQuery(opts), signal },
    );
  },
  dialogForm(ticketId: number, dialogEntityId: string, signal?: AbortSignal) {
    return portalApi.request<ProcessDialogFormData>(
      "GET",
      `/api/portal/process/ticket/${ticketId}/dialog/${enc(dialogEntityId)}/form`,
      { signal },
    );
  },
  createTicket(body: NewProcessTicketIn) {
    return portalApi.request<NewProcessTicketOut>("POST", "/api/portal/process/tickets", { body });
  },
  enroll(ticketId: number, body: { process_entity_id: string; field_values: Record<string, unknown> }) {
    return portalApi.request<TicketProcessState>("POST", `/api/portal/process/ticket/${ticketId}/start`, { body });
  },
  submit(ticketId: number, body: { activity_dialog_entity_id: string; field_values: Record<string, unknown> }) {
    return portalApi.request<ActivityDialogSubmitOut>("POST", `/api/portal/process/ticket/${ticketId}/submit`, {
      body,
    });
  },
  ticketState(ticketId: number, signal?: AbortSignal) {
    return portalApi.request<TicketProcessState>("GET", `/api/portal/process/ticket/${ticketId}/state`, { signal });
  },
};
