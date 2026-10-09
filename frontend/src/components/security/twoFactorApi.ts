import type { Schemas } from "@tiqora/api-client";
import { api } from "@/lib/api";
import { customerSecurityApi } from "@/lib/portalSecurityApi";

export type PasskeyOut = Schemas["PasskeyOut"];

/**
 * What `TwoFactorPanel` needs from a backend: the same screen manages the
 * second factors of an agent (`/api/v1/auth/*`) and of a customer
 * (`/api/portal/auth/*`).
 */
export interface TwoFactorApi {
  status(): Promise<{ totp_enabled: boolean }>;
  enroll(currentCode?: string): Promise<{ secret: string; otpauth_uri: string }>;
  /** `<img src>` of the QR code of the pending enrollment (cookie-authenticated, same-origin). */
  enrollQrUrl(): string;
  confirm(code: string): Promise<{ totp_enabled: boolean }>;
  disable(code: string): Promise<{ totp_enabled: boolean }>;
  listPasskeys(): Promise<PasskeyOut[]>;
  registerBegin(): Promise<unknown>;
  registerFinish(credential: unknown, name: string): Promise<unknown>;
  deletePasskey(id: number): Promise<void>;
  webauthnEnabled(): Promise<boolean>;
}

export const agentTwoFactorApi: TwoFactorApi = {
  async status() {
    const res = await api.totpStatus();
    return { totp_enabled: res.enabled };
  },
  enroll() {
    return api.totpEnroll();
  },
  enrollQrUrl: () => "/api/v1/auth/totp/enroll/qr",
  async confirm(code) {
    const res = await api.totpConfirm({ code });
    return { totp_enabled: res.enabled };
  },
  async disable(code) {
    const res = await api.totpDisable({ code });
    return { totp_enabled: res.enabled };
  },
  listPasskeys: () => api.passkeyList(),
  registerBegin: () => api.passkeyRegisterBegin(),
  registerFinish: (credential, name) =>
    api.passkeyRegisterFinish({
      credential: credential as Record<string, unknown>,
      name: name.trim() || null,
    }),
  deletePasskey: (id) => api.passkeyDelete(id),
  async webauthnEnabled() {
    const methods = await api.authMethods();
    return Boolean(methods.webauthn);
  },
};

export const customerTwoFactorApi: TwoFactorApi = {
  async status() {
    const res = await customerSecurityApi.totpStatus();
    return { totp_enabled: res.enabled };
  },
  enroll: (currentCode) => customerSecurityApi.totpEnroll(currentCode),
  enrollQrUrl: () => "/api/portal/auth/totp/enroll/qr",
  async confirm(code) {
    const res = await customerSecurityApi.totpConfirm(code);
    return { totp_enabled: res.enabled };
  },
  async disable(code) {
    const res = await customerSecurityApi.totpDisable(code);
    return { totp_enabled: res.enabled };
  },
  listPasskeys: () => customerSecurityApi.passkeyList(),
  registerBegin: () => customerSecurityApi.passkeyRegisterBegin(),
  registerFinish: (credential, name) =>
    customerSecurityApi.passkeyRegisterFinish(credential as Record<string, unknown>, name),
  deletePasskey: (id) => customerSecurityApi.passkeyDelete(id),
  async webauthnEnabled() {
    const methods = await customerSecurityApi.methods();
    return Boolean(methods.webauthn);
  },
};
