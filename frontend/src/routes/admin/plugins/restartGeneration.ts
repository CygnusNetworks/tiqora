/** The restart generation a finished install/uninstall job requested, or `null` (none). */
export function jobRestartGeneration(result: Record<string, unknown> | null | undefined): number | null {
  const value = result?.restart_generation;
  return typeof value === "number" ? value : null;
}
