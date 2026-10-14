export type SkippedEntry = { ticket_id: number; reason: string; detail?: string | null };

/** The `skipped` list of a bulk job result. */
export function skippedOf(result: Record<string, unknown> | null | undefined): SkippedEntry[] {
  return Array.isArray(result?.skipped) ? (result.skipped as SkippedEntry[]) : [];
}
