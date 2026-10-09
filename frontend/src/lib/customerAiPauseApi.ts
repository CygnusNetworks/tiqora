/**
 * Per-customer stop of the AI automation
 * (`backend/src/tiqora/api/v1/customers.py`, `…/ai-pause`). Hand-written like
 * `./avatarApi.ts` until the generated schema is wired into the client.
 */
import { api } from "./api";

export type CustomerAiPause = {
  active: boolean;
  /** `null` while active: until an agent lifts it. */
  paused_until: string | null;
  reason: string | null;
  by_name: string | null;
  since: string | null;
};

export type CustomerAiPauseScope = "user" | "company";

const path = (scope: CustomerAiPauseScope, key: string) =>
  scope === "company"
    ? `/api/v1/customers/companies/${encodeURIComponent(key)}/ai-pause`
    : `/api/v1/customers/${encodeURIComponent(key)}/ai-pause`;

export const customerAiPauseApi = {
  get(scope: CustomerAiPauseScope, key: string, signal?: AbortSignal) {
    return api.request<CustomerAiPause>("GET", path(scope, key), { signal });
  },
  /** `days: null` = until lifted. */
  set(scope: CustomerAiPauseScope, key: string, body: { days: number | null; reason?: string }) {
    return api.request<CustomerAiPause>("PUT", path(scope, key), { body });
  },
  lift(scope: CustomerAiPauseScope, key: string) {
    return api.request<void>("DELETE", path(scope, key));
  },
};
