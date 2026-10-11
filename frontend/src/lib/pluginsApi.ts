/**
 * Plugin endpoints (`backend/src/tiqora/api/v1/plugins.py`,
 * `backend/src/tiqora/api/v1/admin/plugins.py`). Hand-written wrappers like
 * `./phoneApi.ts`; the shapes come from the generated schema.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type FrontendPlugin = Schemas["PluginFrontendOut"];
export type FrontendContribution = FrontendPlugin["contributions"][number];
export type PluginAdmin = Schemas["PluginAdminOut"];
export type PluginAdminList = Schemas["PluginAdminListOut"];

const adminPath = (id: string) => `/api/v1/admin/plugins/${encodeURIComponent(id)}`;

export const pluginsApi = {
  /** Active plugins with a web part or translations, as this user may see them. */
  frontend(signal?: AbortSignal): Promise<FrontendPlugin[]> {
    return api.request<FrontendPlugin[]>("GET", "/api/v1/plugins/frontend", { signal });
  },
  adminList(signal?: AbortSignal): Promise<PluginAdminList> {
    return api.request<PluginAdminList>("GET", "/api/v1/admin/plugins", { signal });
  },
  setEnabled(id: string, enabled: boolean): Promise<PluginAdmin> {
    const body: Schemas["PluginEnabledIn"] = { enabled };
    return api.request<PluginAdmin>("PUT", `${adminPath(id)}/enabled`, { body });
  },
  reset(id: string): Promise<PluginAdmin> {
    return api.request<PluginAdmin>("POST", `${adminPath(id)}/reset`);
  },
};
