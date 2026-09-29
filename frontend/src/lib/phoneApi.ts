/**
 * Phone-call endpoints (`backend/src/tiqora/api/v1/phone_calls.py`,
 * `reference.py` caller lookup / phone config / screen dynamic fields).
 * Hand-written wrappers like `./refineApi.ts`; the shapes come from the
 * generated schema.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type PhoneDirection = "inbound" | "outbound";
export type PhoneCallRequest = Schemas["PhoneCallRequest"];
export type PhoneCallResponse = Schemas["PhoneCallResponse"];
export type CallerLookup = Schemas["CallerLookupOut"];
export type CallerCustomer = Schemas["CallerCustomerOut"];
export type CallerTicket = Schemas["CallerTicketOut"];
export type PhoneConfig = Schemas["PhoneConfigOut"];
export type DialScheme = PhoneConfig["dial_scheme"];
export type DynamicFieldDef = Schemas["DynamicFieldDefOut"];
export type ActiveCall = Schemas["ActiveCall"];
export type PhoneExtension = Schemas["PhoneExtensionOut"];
export type PhoneScreen = "AgentTicketPhone" | "AgentTicketPhoneInbound" | "AgentTicketPhoneOutbound";

/** 409 detail of an outbound call on a ticket another agent holds. */
export type PhoneCallLockedDetail = {
  message: string;
  locked_by_id: number | null;
  locked_by_name: string | null;
};

export const phoneApi = {
  logPhoneCall(ticketId: number, body: PhoneCallRequest, signal?: AbortSignal) {
    return api.request<PhoneCallResponse>("POST", `/api/v1/tickets/${ticketId}/phone-calls`, {
      body,
      signal,
    });
  },
  callerLookup(number: string, signal?: AbortSignal) {
    return api.request<CallerLookup>("GET", "/api/v1/reference/caller", {
      query: { number },
      signal,
    });
  },
  phoneConfig(signal?: AbortSignal) {
    return api.request<PhoneConfig>("GET", "/api/v1/reference/phone-config", { signal });
  },
  /** CTI popup: running calls + those ended ≤ 15 min ago (reload restore). */
  activeCalls(signal?: AbortSignal) {
    return api.request<ActiveCall[]>("GET", "/api/v1/phone/calls/active", { signal });
  },
  dismissCall(callId: string) {
    return api.request<void>("POST", `/api/v1/phone/calls/${encodeURIComponent(callId)}/dismiss`);
  },
  myExtension(signal?: AbortSignal) {
    return api.request<PhoneExtension>("GET", "/api/v1/auth/me/phone", { signal });
  },
  setMyExtension(extension: string | null) {
    return api.request<PhoneExtension>("PUT", "/api/v1/auth/me/phone", { body: { extension } });
  },
  screenDynamicFields(screen: PhoneScreen, signal?: AbortSignal) {
    return api.request<DynamicFieldDef[]>("GET", "/api/v1/reference/dynamic-fields", {
      query: { screen },
      signal,
    });
  },
};
