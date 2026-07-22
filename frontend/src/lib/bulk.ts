/** Chunked bulk-update helper for admin bulk actions over large selections. */

import { ApiError } from "@/lib/api";

/**
 * Backend cap on `ids` per bulk PATCH call, e.g.
 * `CustomerUserBulkUpdate.ids` (`backend/src/tiqora/api/v1/admin/schemas.py`,
 * `max_length=1000`). Keep in sync if the backend limit ever changes.
 */
export const BULK_CHUNK_SIZE = 1000;

export type BulkChunkResult = { updated: number };

/**
 * Apply `applyChunk` to `ids` in `BULK_CHUNK_SIZE`-sized chunks, sequentially,
 * summing the `updated` counts and reporting progress after each chunk.
 *
 * If a chunk fails, aborts immediately and rethrows — annotating the error
 * message with how many ids were already applied so the caller can show a
 * useful "partially done" status instead of a bare backend error.
 */
export async function bulkInChunks(
  ids: Array<number | string>,
  applyChunk: (chunk: Array<number | string>) => Promise<BulkChunkResult>,
  onProgress?: (done: number, total: number) => void,
): Promise<BulkChunkResult> {
  const total = ids.length;
  let updated = 0;
  let done = 0;

  for (let i = 0; i < ids.length; i += BULK_CHUNK_SIZE) {
    const chunk = ids.slice(i, i + BULK_CHUNK_SIZE);
    try {
      const result = await applyChunk(chunk);
      updated += result.updated;
    } catch (err) {
      // Language-neutral progress marker — appended to the raw error message,
      // which is shown verbatim in both locales.
      const progress = `${updated}/${total}`;
      if (err instanceof ApiError) {
        throw new ApiError(err.status, `${err.message} (${progress})`, err.path);
      }
      const message = err instanceof Error ? err.message : String(err);
      throw new Error(`${message} (${progress})`);
    }
    done += chunk.length;
    onProgress?.(done, total);
  }

  return { updated };
}
