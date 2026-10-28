import type { MyApproverRow } from "@/lib/approvalsApi";

/**
 * The approver row to vote with. `my_rows` is ordered by deadline and also holds rows the caller covers
 * as a substitute, so "the first one" could cast somebody else's vote: prefer the row the caller came
 * with (the inbox row they clicked), then their own, and only then a substitute row.
 */
export function pickMyRow(rows: MyApproverRow[] | undefined, preferredApproverId?: number | null): MyApproverRow | undefined {
  if (!rows || rows.length === 0) return undefined;
  return (
    (preferredApproverId != null ? rows.find((r) => r.approver_id === preferredApproverId) : undefined) ??
    rows.find((r) => r.on_behalf_of_label == null) ??
    rows[0]
  );
}
