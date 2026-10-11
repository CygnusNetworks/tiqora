import { ChatApiError } from "./api";
import type { Translate } from "./i18n";

export function fmtBytes(n: number): string {
  if (n >= 1024 * 1024) return `${Math.round((n / (1024 * 1024)) * 10) / 10} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

/** Friendly visitor message for an API failure (never a raw code). */
export function errorText(t: Translate, e: unknown, maxBytes = 0): string {
  if (!(e instanceof ChatApiError)) return t("error.generic");
  switch (e.code) {
    case "body_too_large":
      return t("error.bodyTooLarge");
    case "upload_rejected":
      switch (e.errors.file) {
        case "type":
          return t("upload.type");
        case "empty":
          return t("upload.empty");
        case "disabled":
          return t("upload.disabled");
        case "size":
          return t("upload.tooLarge", { max: fmtBytes(maxBytes) });
        default:
          return t("upload.failed");
      }
    case "disabled":
      return t("error.disabled");
    case "network":
      return t("error.network");
    case "chat_unavailable":
      return t("error.unavailable");
    case "forbidden":
      return t("error.notAllowed");
    case "rate_limited":
      return t("error.rateLimited");
  }
  return e.status === 429 ? t("error.rateLimited") : t("error.generic");
}

export function newClientId(): string {
  const b = new Uint8Array(12);
  try {
    crypto.getRandomValues(b);
  } catch {
    for (let i = 0; i < b.length; i++) b[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}
