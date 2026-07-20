/** Ticket priority display helpers.
 *
 * Znuny priorities carry a leading numeric rank in their name (e.g.
 * "3 normal", "5 very high"). That rank is an internal ordering device, not
 * something agents read as part of the label, so the UI shows the bare name.
 */

/** Drop Znuny's leading numeric rank ("3 normal" → "normal"). */
export function priorityName(priority: string | null | undefined): string | null {
  if (!priority) return null;
  return priority.replace(/^\s*\d+\s+/, "");
}

/** Priority text tone by Znuny priority id (1=lowest … 5=highest). */
export function priorityTextClass(priorityId: number | null | undefined): string {
  if (priorityId == null) return "text-ink";
  if (priorityId >= 5) return "text-danger";
  if (priorityId === 4) return "text-warn";
  return "text-ink";
}
