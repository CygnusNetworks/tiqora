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
export type PluginPackage = Schemas["PluginPackageOut"];
export type RestartStatus = Schemas["RestartStatusOut"];
export type PluginRepository = Schemas["RepositoryOut"];
export type Catalog = Schemas["CatalogOut"];
export type CatalogEntry = Schemas["CatalogEntryOut"];

const adminPath = (id: string) => `/api/v1/admin/plugins/${encodeURIComponent(id)}`;
const packagePath = (sha: string) => `/api/v1/admin/plugins/packages/${encodeURIComponent(sha)}`;
const repoPath = (id: number) => `/api/v1/admin/plugin-repositories/${id}`;

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
  /** Uploads a `.tqplugin`; the backend inspects it and answers with the report (nothing is installed yet). */
  uploadPackage(file: File): Promise<PluginPackage> {
    const form = new FormData();
    form.append("file", file);
    return api.request<PluginPackage>("POST", "/api/v1/admin/plugins/packages", { body: form });
  },
  getPackage(sha: string, signal?: AbortSignal): Promise<PluginPackage> {
    return api.request<PluginPackage>("GET", packagePath(sha), { signal });
  },
  deletePackage(sha: string): Promise<void> {
    return api.request<void>("DELETE", packagePath(sha));
  },
  installPackage(sha: string, body: Schemas["PluginInstallIn"]): Promise<Schemas["JobAccepted"]> {
    return api.request<Schemas["JobAccepted"]>("POST", `${packagePath(sha)}/install`, { body });
  },
  uninstall(id: string, body: Schemas["PluginUninstallIn"]): Promise<Schemas["JobAccepted"]> {
    return api.request<Schemas["JobAccepted"]>("POST", `${adminPath(id)}/uninstall`, { body });
  },
  /** The latest coordinated restart, or `null` when none was ever requested. */
  restartStatus(signal?: AbortSignal): Promise<RestartStatus | null> {
    return api.request<RestartStatus | null>("GET", "/api/v1/admin/plugins/restart", { signal });
  },
  requestRestart(reason?: string): Promise<RestartStatus> {
    const body: Schemas["RestartIn"] = { reason: reason ?? null };
    return api.request<RestartStatus>("POST", "/api/v1/admin/plugins/restart", { body });
  },
  repositories(signal?: AbortSignal): Promise<PluginRepository[]> {
    return api.request<PluginRepository[]>("GET", "/api/v1/admin/plugin-repositories", { signal });
  },
  createRepository(body: Schemas["RepositoryIn"]): Promise<PluginRepository> {
    return api.request<PluginRepository>("POST", "/api/v1/admin/plugin-repositories", { body });
  },
  updateRepository(id: number, body: Schemas["RepositoryIn"]): Promise<PluginRepository> {
    return api.request<PluginRepository>("PUT", repoPath(id), { body });
  },
  deleteRepository(id: number): Promise<void> {
    return api.request<void>("DELETE", repoPath(id));
  },
  refreshRepository(id: number): Promise<Schemas["JobAccepted"]> {
    return api.request<Schemas["JobAccepted"]>("POST", `${repoPath(id)}/refresh`);
  },
  catalog(
    params: { q?: string; category?: string; repositoryId?: number } = {},
    signal?: AbortSignal,
  ): Promise<Catalog> {
    const query = new URLSearchParams();
    if (params.q) query.set("q", params.q);
    if (params.category) query.set("category", params.category);
    if (params.repositoryId !== undefined) query.set("repository_id", String(params.repositoryId));
    const qs = query.toString();
    return api.request<Catalog>("GET", `/api/v1/admin/plugin-repositories/catalog${qs ? `?${qs}` : ""}`, {
      signal,
    });
  },
  download(repoId: number, body: Schemas["DownloadIn"]): Promise<Schemas["JobAccepted"]> {
    return api.request<Schemas["JobAccepted"]>("POST", `${repoPath(repoId)}/download`, { body });
  },
};
