/** A colleague picked from the composer's `@` typeahead. */
export type PickedMention = { id: number; name: string };

/**
 * The `@…` token the caret currently sits in, or null. Only a token that
 * starts the text or follows whitespace counts, so an email address in the
 * body never opens the picker.
 */
export function mentionQueryAt(
  text: string,
  caret: number,
): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const match = /(?:^|\s)@([\p{L}\p{N}._-]*)$/u.exec(before);
  if (!match) return null;
  return { start: caret - match[1].length - 1, query: match[1] };
}

/**
 * Mentions whose `@Name` survived editing — what actually gets recorded.
 * Deleting the name from the body drops the mention again, so the text stays
 * the single source of truth. Duplicates are collapsed.
 */
export function survivingMentions(body: string, picked: PickedMention[]): PickedMention[] {
  const seen = new Set<number>();
  return picked.filter((m) => {
    if (seen.has(m.id) || !body.includes(`@${m.name}`)) return false;
    seen.add(m.id);
    return true;
  });
}
