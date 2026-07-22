/** Channel/role presentation helpers shared by the split (master-detail) and
 * conversation (chat-bubble) article views, plus the auto view-mode switch. */
import type { ArticleListItem } from "@/lib/api";
import { parseRecipient, parseRecipientList, formatRecipient } from "@/components/agent/RecipientsField";

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

/** Avatar background/text tint by sender role — feeds `Avatar`'s `tone` prop
 * so the initials fallback reads as a role tint rather than a ring. */
export function avatarTone(senderType: string | null | undefined): "accent" | "customer" {
  return (senderType || "").toLowerCase() === "customer" ? "customer" : "accent";
}

/** Strip surrounding matching quotes (`"…"` or `'…'`) some mail clients wrap
 * a bare display name in when there's no `<email>` part to key off of —
 * `parseRecipient` already strips these when it does match the `Name
 * <email>` shape, this covers the name-only remainder. */
function stripQuotes(raw: string): string {
  return raw.trim().replace(/^["']|["']$/g, "").trim();
}

/**
 * Human-readable sender label for an article's raw `from_address` header:
 * the parsed display name when present, else the parsed (or bare) email,
 * else the trimmed, quote-stripped raw string as a last resort. Never
 * returns the raw string with its RFC-5322 quoting/angle-bracket wrapping
 * intact.
 */
export function senderDisplayName(raw: string | null | undefined): string | null {
  const value = (raw ?? "").trim();
  if (!value) return null;
  const parsed = parseRecipient(value);
  if (parsed) return parsed.name || parsed.email;
  return stripQuotes(value) || null;
}

/** Best-effort initials from an article's parsed sender display name (or, if
 * that fails to parse, its raw `from_address`). Uses the first letters of
 * the first two words — handling both "First Last" and the "Last, First"
 * form Znuny sometimes stores — falling back to the first two characters of
 * the mailbox-local part of the parsed (or bare) email address. */
export function initialsFor(a: ArticleListItem): string {
  const raw = a.from_address || "";
  const parsed = parseRecipient(raw.trim());
  const name = parsed?.name || (parsed ? "" : stripQuotes(raw));
  const words = name
    .replace(/,/g, " ")
    .replace(/[._-]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();

  const email = parsed?.email || raw.split("@")[0];
  const local = (email || "?").split("@")[0] || "?";
  const localParts = local.replace(/[._-]+/g, " ").trim().split(/\s+/).filter(Boolean);
  const letters = localParts.length >= 2 ? localParts[0][0] + localParts[1][0] : local.slice(0, 2) || "?";
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

/** Render a raw `from_address` header ("Name <mail@host>", quoted or not) as
 * clean "Name <mail@host>" — same shape, quotes stripped. Falls back to the
 * trimmed raw string when it doesn't parse as an address at all. */
export function formatFromAddress(raw: string | null | undefined): string {
  const parsed = parseRecipient((raw ?? "").trim());
  return parsed ? formatRecipient(parsed) : (raw ?? "").trim();
}

/** Same as `formatFromAddress`, but for a comma-joined `to_address` header
 * with (potentially) multiple recipients. */
export function formatToAddresses(raw: string | null | undefined): string {
  const recipients = parseRecipientList(raw);
  return recipients.length ? recipients.map(formatRecipient).join(", ") : (raw ?? "").trim();
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
