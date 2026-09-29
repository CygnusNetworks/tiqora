import { useQuery } from "@tanstack/react-query";
import { api, type ArticleListItem, type ArticleSecurity } from "@/lib/api";

export type SecurityTone = "success" | "warn" | "danger" | "muted";

const STATUS_TONE: Record<string, SecurityTone> = {
  verified: "success",
  decrypted: "success",
  signed_untrusted: "warn",
  unknown_key: "warn",
  verify_failed: "danger",
  decrypt_failed: "danger",
};

/** Tone + glyph of an article's PGP/S-MIME result: 🔒 encrypted,
 * ✓ good signature, ⚠ anything the agent should not trust blindly. */
export function securityLook(security: ArticleSecurity): { tone: SecurityTone; icon: string } {
  const tone = STATUS_TONE[security.status] ?? "muted";
  if (tone !== "success") return { tone, icon: "⚠" };
  return { tone, icon: security.encrypted ? "🔒" : "✓" };
}

/** The article's security result: from the list row, or — for legacy Znuny
 * articles decrypted on view — from the body response, which computes it
 * the first time. Shares the body query the reader already runs. */
export function useArticleSecurity(
  ticketId: number,
  article: ArticleListItem,
  { fromBody = true }: { fromBody?: boolean } = {},
): ArticleSecurity | null {
  const bodyQ = useQuery({
    queryKey: ["tickets", ticketId, "articles", article.id, "body"],
    queryFn: () => api.getArticleBody(ticketId, article.id),
    enabled: fromBody && !article.security,
  });
  return article.security ?? (fromBody ? bodyQ.data?.security ?? null : null);
}
