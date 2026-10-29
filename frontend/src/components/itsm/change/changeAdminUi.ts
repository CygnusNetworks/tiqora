/**
 * Shared bits of the change administration (`/admin/itsm/change`): the text of a failed admin call
 * and the mapping between CAB members and the approval engine's approver entries.
 */
import type { TFunction } from "i18next";
import type { ApproverEntry } from "@/components/approvals/ApproverEntryEditor";
import { ApiError } from "@/lib/api";
import type { AdminCabMemberIn } from "@/lib/changeAdminApi";
import { changeErrorCode, changeErrorDetail } from "@/lib/changeApi";
import { changeErrorText } from "./changeUi";

/** Numbers and names the backend adds to an error code, used in the texts (`max`, `key`, …). */
const PARAMS = ["max", "max_tasks", "key", "ref", "member_kind", "meetings", "question"] as const;

/**
 * Text of a failed admin write or read. Both 422 shapes: the change API's `{"detail": {"code", …}}`
 * (labelled under `itsm.change.errors`, with its numbers) and FastAPI's validation list
 * (`{"detail": [{loc, msg}]}`), which names the field instead of showing the English message.
 */
export function changeAdminErrorText(err: unknown, t: TFunction): string {
  if (err instanceof ApiError) {
    const body = err.detail as { detail?: unknown } | null;
    const inner = body && typeof body === "object" ? body.detail : null;
    if (Array.isArray(inner)) {
      const loc = (inner[0] as { loc?: unknown } | undefined)?.loc;
      const field = Array.isArray(loc) ? loc.filter((p) => p !== "body").join(".") : "";
      return field ? t("itsm.change.errors.validation", { field }) : t("itsm.change.errors.invalid_input");
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
