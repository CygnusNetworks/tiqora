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

export type ConcurrentResult<Id> = { succeeded: Id[]; failed: Id[] };

/**
 * Apply `apply` to each of `ids` with at most `concurrency` in flight at
 * once — used for per-ticket PATCH loops (no bulk endpoint exists for
 * tickets, unlike the admin resources `bulkInChunks` serves). Failures are
 * collected per id rather than aborting the whole run, so the caller can
 * report a partial result and keep the failed ids selected for a retry.
 */
export async function runConcurrent<Id>(
  ids: Id[],
  apply: (id: Id) => Promise<void>,
  concurrency: number,
  onProgress?: (done: number, total: number) => void,
): Promise<ConcurrentResult<Id>> {
  const total = ids.length;
  const succeeded: Id[] = [];
  const failed: Id[] = [];
  let done = 0;
  let next = 0;

  async function worker() {
    while (next < ids.length) {
      const id = ids[next++];
      try {
        await apply(id);
        succeeded.push(id);
      } catch {
        failed.push(id);
      }
      done += 1;
      onProgress?.(done, total);
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(concurrency, ids.length) }, () => worker()),
  );
  return { succeeded, failed };
}
