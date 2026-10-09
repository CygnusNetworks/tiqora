/**
 * Avatar uploads, vCard import and the admin avatar sources
 * (`backend/src/tiqora/api/v1/avatars.py`). Hand-written wrappers like
 * `./phoneApi.ts`; the shapes are written out here until the generated
 * schema carries them. Reading pictures needs no wrapper — `<img>` loads
 * `/api/v1/avatars/…` directly (see `./avatar.ts`).
 */
import { ApiError, api } from "./api";

export type AvatarUploadOut = { version: string };

export type VCardPreviewCard = {
  /** Position in the file — what `apply` refers to. */
  index: number;
  display_name: string;
  first_name: string | null;
  last_name: string | null;
  emails: string[];
  phone: string | null;
  mobile: string | null;
  org: string | null;
  title: string | null;
  has_photo: boolean;
  /** `data:image/png;base64,…` thumbnail. */
  photo_preview: string | null;
  /** Customer matched by any of the card's addresses. */
  match_login: string | null;
  match_name: string | null;
  /** Blank fields on the target (match, or the `login` asked for) the card would fill. */
  fill_fields: string[];
  /** Fields the target has with another value than the card. */
  conflicts: VCardConflict[];
  has_existing_avatar: boolean;
};

export type VCardField = "first_name" | "last_name" | "email" | "phone" | "mobile";

export type VCardConflict = { field: VCardField; current: string; incoming: string };

export type VCardImportPreview = { cards: VCardPreviewCard[]; truncated: boolean };

export type VCardSelection = {
  index: number;
  login: string;
  photo: boolean;
  fields: boolean;
  /** Conflicting fields to overwrite with the card's value. */
  replace?: VCardField[];
};

export type VCardImportApplyOut = {
  applied: { login: string; photo: boolean; filled: string[]; replaced: string[] }[];
};

export type AvatarSettings = { gravatar_enabled: boolean; favicon_enabled: boolean };

const fileForm = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return form;
};

export const avatarApi = {
  uploadCustomerAvatar(login: string, file: File) {
    return api.request<AvatarUploadOut>(
      "PUT",
      `/api/v1/customers/${encodeURIComponent(login)}/avatar`,
      { body: fileForm(file) },
    );
  },
  deleteCustomerAvatar(login: string) {
    return api.request<void>("DELETE", `/api/v1/customers/${encodeURIComponent(login)}/avatar`);
  },
  uploadCompanyLogo(customerId: string, file: File) {
    return api.request<AvatarUploadOut>(
      "PUT",
      `/api/v1/customers/companies/${encodeURIComponent(customerId)}/logo`,
      { body: fileForm(file) },
    );
  },
  deleteCompanyLogo(customerId: string) {
    return api.request<void>(
      "DELETE",
      `/api/v1/customers/companies/${encodeURIComponent(customerId)}/logo`,
    );
  },
  /** `login`: compare every card with that customer instead of its address match. */
  previewVcards(file: File, login?: string) {
    const form = fileForm(file);
    if (login) form.append("login", login);
    return api.request<VCardImportPreview>("POST", "/api/v1/customers/vcard-import/preview", {
      body: form,
    });
  },
  applyVcards(file: File, selections: VCardSelection[]) {
    const form = fileForm(file);
    form.append("selections", JSON.stringify(selections));
    return api.request<VCardImportApplyOut>("POST", "/api/v1/customers/vcard-import/apply", {
      body: form,
    });
  },
  getSettings(signal?: AbortSignal) {
    return api.request<AvatarSettings>("GET", "/api/v1/admin/avatars", { signal });
  },
  putSettings(body: AvatarSettings) {
    return api.request<AvatarSettings>("PUT", "/api/v1/admin/avatars", { body });
  },
};

/**
 * Readable message of a failed call. `ApiError.detail` is the whole response
 * body. Understood shapes of `body.detail`: a plain string, an object with a
 * string `message` (`{message}`, `{message, path}`, `{message, errors}`), and a
 * validation list (`[{msg}]`) — never "[object Object]".
 */
export function apiErrorMessage(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError)) return fallback;
  const body = err.detail as { detail?: unknown } | string | null;
  // A proxy's HTML error page (e.g. nginx 413) is no message.
  if (typeof body === "string") return body.trim() && !body.trim().startsWith("<") ? body.trim() : fallback;
  // Wave-0 shapes without a `detail` wrapper: `{message}` / `{message, path}`.
  const top = body && typeof body === "object" ? (body as { message?: unknown }).message : null;
  if (typeof top === "string" && top && !(body as { detail?: unknown }).detail) return top;
  const inner = body && typeof body === "object" ? body.detail : null;
  if (typeof inner === "string" && inner) return inner;
  // `{"detail": {"message": "…", "errors": {…}}}` (admin identity API).
  if (inner && typeof inner === "object" && !Array.isArray(inner)) {
    const message = (inner as { message?: unknown }).message;
    if (typeof message === "string" && message) return message;
  }
  if (Array.isArray(inner)) {
    const msg = (inner[0] as { msg?: unknown } | undefined)?.msg;
    if (typeof msg === "string" && msg) return msg;
  } else if (inner && typeof inner === "object") {
    const msg = (inner as { message?: unknown }).message;
    if (typeof msg === "string" && msg) return msg;
  }
  return fallback;
}
