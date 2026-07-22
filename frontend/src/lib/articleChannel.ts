/** Channel/role presentation helpers shared by the split (master-detail) and
 * conversation (chat-bubble) article views, plus the auto view-mode switch. */
import type { ArticleListItem } from "@/lib/api";
import { parseRecipient } from "@/components/agent/RecipientsField";

/**
 * Standard Znuny `communication_channel` seed order (see
 * backend/src/tiqora/bootstrap/schema/initial_insert.*.sql): 1=Email,
 * 2=Phone, 3=Internal, 4=Chat. `ArticleListItem` only carries the numeric
 * id, so filtering/icons/auto-detection key off these stable ids rather
 * than a name.
 */
export const CHANNEL_EMAIL = 1;
export const CHANNEL_PHONE = 2;
export const CHANNEL_INTERNAL = 3;
export const CHANNEL_CHAT = 4;

/**
 * Channels considered "conversational" for the auto view-switch heuristic
 * (dominant channel → default to the chat-bubble view). This Znuny install's
 * `communication_channel` table only ever seeds Email/Phone/Internal/Chat —
 * there is no whatsapp/sms row to match against — so `Chat` (id 4) is the
 * closest available proxy for the "whatsapp/sms/chat" rule of thumb.
 */
const CONVERSATIONAL_CHANNELS = new Set<number>([CHANNEL_CHAT]);

export function isConversationalChannel(channelId: number): boolean {
  return CONVERSATIONAL_CHANNELS.has(channelId);
}

export function channelIcon(channelId: number): string {
  if (channelId === CHANNEL_EMAIL) return "✉";
  if (channelId === CHANNEL_PHONE) return "📞";
  if (channelId === CHANNEL_INTERNAL) return "📝";
  if (channelId === CHANNEL_CHAT) return "💬";
  return "✉";
}

export function senderRingClass(senderType: string | null | undefined, channelId: number): string {
  if (channelId === CHANNEL_INTERNAL) return "ring-2 ring-hairline";
  const s = (senderType || "").toLowerCase();
  if (s === "customer") return "ring-2 ring-green/60";
  return "ring-2 ring-accent/60";
}

/** Best-effort initials from the mailbox-local part of `from_address`. */
export function initialsFor(a: ArticleListItem): string {
  const local = (a.from_address || "?").split("@")[0] || "?";
  const parts = local.replace(/[._-]+/g, " ").trim().split(/\s+/).filter(Boolean);
  const letters = parts.length >= 2 ? parts[0][0] + parts[1][0] : local.slice(0, 2);
  return letters.toUpperCase();
}

/**
 * Extract the bare address out of an article's `from_address`, which arrives
 * in either mail form ("Name <mail@host>") or a bare address — same shapes
 * `parseRecipient` (RecipientsField.tsx) already handles for the compose
 * side, reused here rather than re-implementing the angle-bracket parsing.
 * Returns undefined when nothing plausible is present, so callers can hand
 * it straight to `Avatar`'s optional `email` prop (Gravatar lookup, d=404
 * falls back to initials).
 */
export function emailFromAddress(raw: string | null | undefined): string | undefined {
  return parseRecipient(raw ?? "")?.email;
}

/** A note that isn't visible to the customer, on the internal channel — the
 * article rendered as a centered pill rather than a side bubble/list row. */
export function isInternalNote(a: ArticleListItem): boolean {
  return a.communication_channel_id === CHANNEL_INTERNAL && !a.is_visible_for_customer;
}

/**
 * Most common channel among an article set — prefers customer-authored
 * articles (their channel is what the customer actually used to reach out),
 * falling back to all articles when there are none. Returns null for an
 * empty list. Drives the split/conversation auto view-switch.
 */
export function dominantChannel(articles: ArticleListItem[]): number | null {
  const customerArticles = articles.filter(
    (a) => (a.sender_type || "").toLowerCase() === "customer",
  );
  const pool = customerArticles.length > 0 ? customerArticles : articles;
  if (pool.length === 0) return null;
  const counts = new Map<number, number>();
  for (const a of pool) {
    counts.set(a.communication_channel_id, (counts.get(a.communication_channel_id) ?? 0) + 1);
  }
  let best: number | null = null;
  let bestCount = -1;
  for (const [id, count] of counts) {
    if (count > bestCount) {
      best = id;
      bestCount = count;
    }
  }
  return best;
}
