import type { Schemas } from "@tiqora/api-client";
import { portalApi } from "./portalApi";

export type PortalAuthMethods = Schemas["PortalAuthMethodsOut"];
export type CustomerLoginResponse = Schemas["CustomerLoginResponse"];

const BASE = "/api/portal/auth";

/** Customer portal sign-in methods and second factors (`backend/src/tiqora/api/portal/security.py`). */
export const customerSecurityApi = {
  methods(signal?: AbortSignal) {
    return portalApi.request<PortalAuthMethods>("GET", `${BASE}/methods`, { signal });
  },
  totpStatus(signal?: AbortSignal) {
    return portalApi.request<Schemas["TOTPStatusOut"]>("GET", `${BASE}/totp/status`, { signal });
  },
  totpEnroll(currentCode?: string, signal?: AbortSignal) {
    return portalApi.request<Schemas["TOTPEnrollOut"]>("POST", `${BASE}/totp/enroll`, {
      body: currentCode ? { current_code: currentCode } : undefined,
      signal,
    });
  },
  totpConfirm(code: string, signal?: AbortSignal) {
    return portalApi.request<Schemas["TOTPStatusOut"]>("POST", `${BASE}/totp/confirm`, {
      body: { code },
      signal,
    });
  },
  totpDisable(code: string, signal?: AbortSignal) {
    return portalApi.request<Schemas["TOTPStatusOut"]>("DELETE", `${BASE}/totp`, {
      body: { code },
      signal,
    });
  },
  totpVerify(code: string, signal?: AbortSignal) {
    return portalApi.request<CustomerLoginResponse>("POST", `${BASE}/totp/verify`, {
      body: { code },
      signal,
    });
  },
  passkeyList(signal?: AbortSignal) {
    return portalApi.request<Schemas["PasskeyOut"][]>("GET", `${BASE}/passkey`, { signal });
  },
  passkeyRegisterBegin(signal?: AbortSignal) {
    return portalApi.request<Record<string, unknown>>("POST", `${BASE}/passkey/register/begin`, {
      signal,
    });
  },
  passkeyRegisterFinish(credential: Record<string, unknown>, name: string, signal?: AbortSignal) {
    return portalApi.request<Schemas["PasskeyStatusOut"]>(
      "POST",
      `${BASE}/passkey/register/finish`,
      { body: { credential, name: name.trim() || null }, signal },
    );
  },
  passkeyDelete(id: number, signal?: AbortSignal) {
    return portalApi.request<void>("DELETE", `${BASE}/passkey/${id}`, { signal });
  },
  passkeyAuthenticateBegin(signal?: AbortSignal) {
    return portalApi.request<Record<string, unknown>>(
      "POST",
      `${BASE}/passkey/authenticate/begin`,
      { signal },
    );
  },
  passkeyAuthenticateFinish(credential: Record<string, unknown>, signal?: AbortSignal) {
    return portalApi.request<CustomerLoginResponse>(
      "POST",
      `${BASE}/passkey/authenticate/finish`,
      { body: { credential }, signal },
    );
  },
};
