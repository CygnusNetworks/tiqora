/**
 * `POST /api/v1/reference/field-options` (ACL-filtered options of ticket
 * fields incl. dynamic fields). Hand-written wrapper like `./phoneApi.ts`.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type FieldOptionsRequest = Schemas["FieldOptionsRequest"];
export type FieldOptionsContext = Schemas["FieldOptionsContext"];
export type FieldOptionsOut = Schemas["FieldOptionsOut"];
/** Request body as the screens build it: the context's server defaults may be left out. */
export type FieldOptionsBody = Omit<FieldOptionsRequest, "context"> & {
  context?: Partial<FieldOptionsContext>;
};

export const fieldOptionsApi = {
  fetch(body: FieldOptionsBody, signal?: AbortSignal) {
    return api.request<FieldOptionsOut>("POST", "/api/v1/reference/field-options", { body, signal });
  },
};
