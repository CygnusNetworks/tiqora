/**
 * Endpoints behind the plugin extension points (`backend/src/tiqora/api/v1/admin/plugins.py`,
 * `.../admin/notification_events.py`, `.../admin/dynamic_fields.py`, `.../channels.py`,
 * `.../customers.py`). Hand-written wrappers like `./pluginsApi.ts`.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type NumberGenerators = Schemas["NumberGeneratorsOut"];
export type NumberGeneratorOption = Schemas["NumberGeneratorOut"];
export type NotificationTransport = Schemas["NotificationTransportOut"];
export type DynamicFieldPluginType = Schemas["DynamicFieldPluginTypeOut"];
export type PluginChannel = Schemas["PluginChannelOut"];
export type ExternalCustomerImported = Schemas["CustomerUserOut"];

export const pluginPointsApi = {
  numberGenerators(signal?: AbortSignal): Promise<NumberGenerators> {
    return api.request<NumberGenerators>("GET", "/api/v1/admin/plugins/number-generators", { signal });
  },
  /** `null` goes back to the Znuny scheme. */
  setNumberGenerator(key: string | null): Promise<NumberGenerators> {
    const body: Schemas["NumberGeneratorIn"] = { key };
    return api.request<NumberGenerators>("PUT", "/api/v1/admin/plugins/number-generators", { body });
  },
  notificationTransports(signal?: AbortSignal): Promise<NotificationTransport[]> {
    return api.request<NotificationTransport[]>("GET", "/api/v1/admin/notification-events/transports", { signal });
  },
  dfPluginTypes(signal?: AbortSignal): Promise<DynamicFieldPluginType[]> {
    return api.request<DynamicFieldPluginType[]>("GET", "/api/v1/admin/dynamic-fields/plugin-types", { signal });
  },
  pluginChannels(signal?: AbortSignal): Promise<PluginChannel[]> {
    return api.request<PluginChannel[]>("GET", "/api/v1/channels/plugin", { signal });
  },
  externalCustomerImport(source: string, login: string): Promise<ExternalCustomerImported> {
    const body: Schemas["ExternalImportRequest"] = { source, login };
    return api.request<ExternalCustomerImported>("POST", "/api/v1/customers/external-import", { body });
  },
};
