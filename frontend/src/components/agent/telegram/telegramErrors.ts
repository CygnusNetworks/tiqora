import { ApiError } from "@/lib/api";

/**
 * The API's own reason for a refusal (`{"detail": "..."}` on a 4xx) — worth
 * showing verbatim, e.g. Telegram's "older than 48 h". Null for anything
 * else: a validation list (422 `detail` is an array), a proxy's HTML page
 * (non-JSON body arrives as a string), or a 5xx.
 */
export function readableApiReason(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status >= 500) return null;
  const { detail } = err;
  if (detail === null || typeof detail !== "object") return null;
  const reason = (detail as { detail?: unknown }).detail;
  return typeof reason === "string" && reason.trim() ? reason : null;
}

/**
 * A failure that doesn't say whether the backend finished the request: a 5xx,
 * or a non-JSON body (nginx gives up after 90 s while the API may still
 * deliver). Resending blindly could send the message twice.
 */
export function isUncertainFailure(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  return err.status >= 500 || err.detail === null || typeof err.detail !== "object";
}
