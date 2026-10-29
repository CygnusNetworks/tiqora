/**
 * Shared bits of the change administration (`/admin/itsm/change`): the text of a failed admin call
 * and the mapping between CAB members and the approval engine's approver entries.
 */
import { useQuery } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import type { ApproverEntry } from "@/components/approvals/ApproverEntryEditor";
import { ApiError, api } from "@/lib/api";
import type { AdminCabMemberIn } from "@/lib/changeAdminApi";
import { changeErrorCode, changeErrorDetail } from "@/lib/changeApi";
import { changeErrorText } from "./changeUi";

/** Numbers and names the backend adds to an error code, used in the texts (`max`, `key`, …). */
const PARAMS = ["max", "max_tasks", "key", "ref", "member_kind", "meetings", "question"] as const;

/**
 * Text of a failed admin write or read. Both 422 shapes: the change API's `{"detail": {"code", …}}`
 * (labelled under `itsm.change.errors`, with its numbers) and FastAPI's validation list
 * (`{"detail": [{loc, msg}]}`), which names the first field (and how many more) instead of showing the
 * English message.
 */
export function changeAdminErrorText(err: unknown, t: TFunction): string {
  if (err instanceof ApiError) {
    const body = err.detail as { detail?: unknown } | null;
    const inner = body && typeof body === "object" ? body.detail : null;
    if (Array.isArray(inner)) {
      const loc = (inner[0] as { loc?: unknown } | undefined)?.loc;
      const field = Array.isArray(loc) ? loc.filter((p) => p !== "body").join(".") : "";
      if (!field) return t("itsm.change.errors.invalid_input");
      const more = inner.length - 1;
      return more > 0 ? t("itsm.change.errors.validationMore", { field, count: more }) : t("itsm.change.errors.validation", { field });
    }
  }
  const code = changeErrorCode(err);
  if (!code || code === "approval_request_failed") return changeErrorText(err, t);
  const detail = changeErrorDetail(err) ?? {};
  const params: Record<string, unknown> = {};
  for (const name of PARAMS) if (detail[name] !== undefined) params[name] = detail[name];
  return t([`itsm.change.errors.${code}`, "itsm.change.errors.generic"], params);
}

/** A stored CAB member as the approver picker's row, and back. */
export const memberToEntry = (m: { member_kind: ApproverEntry["kind"]; member_ref: string }): ApproverEntry => ({
  kind: m.member_kind,
  ref: m.member_ref,
});
export const entryToMember = (e: ApproverEntry): AdminCabMemberIn => ({ member_kind: e.kind, member_ref: e.ref });

/** `<input type="number">` text → integer, or null when empty or not a number. */
export function intOrNull(text: string): number | null {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isInteger(n) ? n : null;
}

/** The services of the pickers and lists (`GET /reference/services`), shared by the admin tabs. */
export function useReferenceServices() {
  return useQuery({
    queryKey: ["reference", "services"],
    queryFn: ({ signal }) => api.listReferenceServices(signal),
    staleTime: 60_000,
  });
}
