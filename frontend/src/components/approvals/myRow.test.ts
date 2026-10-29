import { describe, expect, it } from "vitest";
import type { MyApproverRow } from "@/lib/approvalsApi";
import { pickMyRow } from "./myRow";

const row = (approver_id: number, on_behalf_of_label: string | null = null): MyApproverRow => ({
  approver_id,
  step_id: 1,
  on_behalf_of_label,
  can_forward: false,
});

describe("pickMyRow", () => {
  it("is undefined without rows", () => {
    expect(pickMyRow(undefined)).toBeUndefined();
    expect(pickMyRow([])).toBeUndefined();
  });
  it("prefers the row the caller came with", () => {
    expect(pickMyRow([row(1), row(2, "Erika Muster")], 2)?.approver_id).toBe(2);
  });
  it("otherwise takes the caller's own row before a substitute row, whatever the order", () => {
    expect(pickMyRow([row(2, "Erika Muster"), row(1)])?.approver_id).toBe(1);
    expect(pickMyRow([row(2, "Erika Muster"), row(1)], 99)?.approver_id).toBe(1);
  });
  it("falls back to a substitute row when that is all there is", () => {
    expect(pickMyRow([row(2, "Erika Muster")])?.on_behalf_of_label).toBe("Erika Muster");
  });
});
