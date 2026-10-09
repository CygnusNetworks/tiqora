/**
 * Deployed-revision status for admins (`/api/v1/admin/deploy-status`, `/admin/processes`,
 * `/admin/deploy`). Hand-written wrapper like `./processApi.ts`.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type DeployStatus = Schemas["DeployStatusOut"];
export type DeployKindStatus = Schemas["DeployKindStatusOut"];
export type AdminProcess = Schemas["AdminProcessOut"];
export type DeployKind = Schemas["DeployIn"]["kind"];
export type DeployResult = Schemas["DeployOut"];

export const deployStatusApi = {
  status(signal?: AbortSignal) {
    return api.request<DeployStatus>("GET", "/api/v1/admin/deploy-status", { signal });
  },
  processes(signal?: AbortSignal) {
    return api.request<AdminProcess[]>("GET", "/api/v1/admin/processes", { signal });
  },
  /** Tiqora's own "Bereitstellen" (only where `can_deploy_from_tiqora`). */
  deploy(kind: DeployKind) {
    return api.request<DeployResult>("POST", "/api/v1/admin/deploy", { body: { kind } });
  },
};
