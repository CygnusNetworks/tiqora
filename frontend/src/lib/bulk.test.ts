import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { BULK_CHUNK_SIZE, bulkInChunks } from "./bulk";

const ids = Array.from({ length: 2988 }, (_, i) => i + 1);

describe("bulkInChunks", () => {
  it("splits a selection over the backend cap, each id once, and sums the result", async () => {
    const apply = vi.fn(async (chunk: Array<number | string>) => ({ updated: chunk.length }));
    const progress = vi.fn();

    await expect(bulkInChunks(ids, apply, progress)).resolves.toEqual({ updated: 2988 });

    expect(BULK_CHUNK_SIZE).toBe(1000);
    expect(apply.mock.calls.map(([chunk]) => chunk.length)).toEqual([1000, 1000, 988]);
    expect(new Set(apply.mock.calls.flatMap(([chunk]) => chunk)).size).toBe(2988);
    expect(progress).toHaveBeenLastCalledWith(2988, 2988);
  });

  it("stops at a failing chunk and names the progress in the error", async () => {
    const apply = vi
      .fn<(chunk: Array<number | string>) => Promise<{ updated: number }>>()
      .mockResolvedValueOnce({ updated: 1000 })
      .mockRejectedValueOnce(new ApiError(422, "boom", "/admin/customer-users/bulk"));

    await expect(bulkInChunks(ids, apply)).rejects.toThrow("boom (1000/2988)");
    expect(apply).toHaveBeenCalledTimes(2);
  });
});
