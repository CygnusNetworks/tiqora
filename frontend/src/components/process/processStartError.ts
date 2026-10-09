import { ApiError } from "@/lib/api";
import { apiErrorMessage } from "@/lib/avatarApi";

/**
 * Readable German message for a failed process request (start, enroll, dialog load or submit): 400
 * (missing / invalid value), 403 (no permission), 404 (gone) and 409 (ACL / state forbids it) get a fixed
 * plain text. The backend details are English exception strings with internal field names, never UI copy.
 * Anything else (network, 5xx) keeps the server detail via `apiErrorMessage`, falling back to `fallbackKey`.
 */
export function processStartErrorMessage(
  err: unknown,
  t: (key: string) => string,
  fallbackKey: string,
  conflictKey = "process.start.conflict",
  forbiddenKey = "process.start.forbidden",
): string {
  if (err instanceof ApiError && err.status === 400) return t("process.form.invalid");
  if (err instanceof ApiError && err.status === 403) return t(forbiddenKey);
  if (err instanceof ApiError && err.status === 404) return t("process.start.notFound");
  if (err instanceof ApiError && err.status === 409) return t(conflictKey);
  return apiErrorMessage(err, t(fallbackKey));
}
