import { ApiError } from "@/lib/api";
import { apiErrorMessage } from "@/lib/avatarApi";

/**
 * Readable German message for a failed start: 403 (no permission), 404 (gone) and 409 (ACL / state forbids the
 * start, from the server's assert_can_start) get a fixed plain text, anything else the server's detail.
 */
export function processStartErrorMessage(err: unknown, t: (key: string) => string, fallbackKey: string): string {
  if (err instanceof ApiError && err.status === 403) return t("process.start.forbidden");
  if (err instanceof ApiError && err.status === 404) return t("process.start.notFound");
  if (err instanceof ApiError && err.status === 409) {
    return apiErrorMessage(err, t("process.start.conflict"));
  }
  return apiErrorMessage(err, t(fallbackKey));
}
