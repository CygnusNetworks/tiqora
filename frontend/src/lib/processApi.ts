/**
 * Process endpoints (`/api/v1/process`) typed from the generated schema. Hand-written wrapper
 * like `./phoneApi.ts`.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type ProcessSummary = Schemas["ProcessSummaryOut"];
export type TicketProcessState = Schemas["TicketProcessStateOut"];
export type ProcessDialogFormData = Schemas["ProcessDialogFormOut"];
export type ProcessDialogFieldData = Schemas["ProcessDialogFieldOut"];
export type NewProcessTicketIn = Schemas["NewProcessTicketIn"];
export type NewProcessTicketOut = Schemas["NewProcessTicketOut"];
export type ActivityDialogSubmitOut = Schemas["ActivityDialogSubmitOut"];
export type TicketAclActions = Schemas["TicketAclActionsOut"];
export type ProcessDetail = Schemas["ProcessDetailOut"];

const enc = encodeURIComponent;
const ticketQuery = (opts?: { ticketId?: number }) =>
  opts?.ticketId === undefined ? undefined : { ticket_id: opts.ticketId };

export const processApi = {
  listStartable(opts?: { ticketId?: number }, signal?: AbortSignal) {
    return api.request<ProcessSummary[]>("GET", "/api/v1/process/", { query: ticketQuery(opts), signal });
  },
  startForm(processEntityId: string, opts?: { ticketId?: number }, signal?: AbortSignal) {
    return api.request<ProcessDialogFormData>("GET", `/api/v1/process/${enc(processEntityId)}/start-form`, {
      query: ticketQuery(opts),
      signal,
    });
  },
  dialogForm(ticketId: number, dialogEntityId: string, signal?: AbortSignal) {
    return api.request<ProcessDialogFormData>(
      "GET",
      `/api/v1/process/ticket/${ticketId}/dialog/${enc(dialogEntityId)}/form`,
      { signal },
    );
  },
  createTicket(body: NewProcessTicketIn) {
    return api.request<NewProcessTicketOut>("POST", "/api/v1/process/tickets", { body });
  },
  enroll(ticketId: number, body: { process_entity_id: string; field_values: Record<string, unknown> }) {
    return api.request<TicketProcessState>("POST", `/api/v1/process/ticket/${ticketId}/start`, { body });
  },
  submit(ticketId: number, body: { activity_dialog_entity_id: string; field_values: Record<string, unknown> }) {
    return api.request<ActivityDialogSubmitOut>("POST", `/api/v1/process/ticket/${ticketId}/submit`, { body });
  },
  ticketState(ticketId: number, signal?: AbortSignal) {
    return api.request<TicketProcessState>("GET", `/api/v1/process/ticket/${ticketId}/state`, { signal });
  },
  /** One process with its activities and their dialogs (read-only admin view). */
  detail(processEntityId: string, signal?: AbortSignal) {
    return api.request<ProcessDetail>("GET", `/api/v1/process/${enc(processEntityId)}`, { signal });
  },
  aclActions(ticketId: number, signal?: AbortSignal) {
    return api.request<TicketAclActions>("GET", `/api/v1/tickets/${ticketId}/acl-actions`, { signal });
  },
};
