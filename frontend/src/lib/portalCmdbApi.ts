/** Customer-portal CI view (`/api/portal/config-items`, `backend/src/tiqora/api/portal/cmdb.py`). */
import type { Schemas } from "@tiqora/api-client";
import { portalApi } from "./portalApi";

export type PortalCi = Schemas["PortalCiOut"];
export type PortalCiPage = Schemas["PortalCiPage"];
export type PortalCiDetail = Schemas["PortalCiDetailOut"];

const BASE = "/api/portal/config-items";

export const portalCmdbApi = {
  list(query: { q?: string; offset?: number; limit?: number } = {}) {
    return portalApi.request<PortalCiPage>("GET", BASE, { query });
  },
  get(id: number) {
    return portalApi.request<PortalCiDetail>("GET", `${BASE}/${id}`);
  },
};
