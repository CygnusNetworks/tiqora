import { createContext } from "react";

/** Which plugin the components below belong to; set by the host around every
 * plugin slot instance and read by the SDK hooks (usePluginApi, …). */
export const PluginInstanceContext = createContext<{ pluginId: string; version: string } | null>(null);
