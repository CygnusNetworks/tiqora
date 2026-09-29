/**
 * Email security of a notification as Znuny's AdminNotificationEvent stores
 * it in notification_event_item rows (read by Znuny and Tiqora alike):
 * EmailSecuritySettings ("1"), EmailSigningCrypting (PGPSign … SMIMESignCrypt),
 * EmailMissingSigningKeys / EmailMissingCryptingKeys (Skip | Send).
 */

export const SECURITY_ITEM_KEYS = [
  "EmailSecuritySettings",
  "EmailSigningCrypting",
  "EmailMissingSigningKeys",
  "EmailMissingCryptingKeys",
] as const;

export const SECURITY_LEVELS = {
  pgp: ["PGPSign", "PGPCrypt", "PGPSignCrypt"],
  smime: ["SMIMESign", "SMIMECrypt", "SMIMESignCrypt"],
} as const;

export type Items = Record<string, string[]>;

export type SecurityForm = {
  enabled: boolean;
  level: string;
  missingSign: string;
  missingCrypt: string;
};

export const firstItem = (items: Items, key: string) => items[key]?.[0] ?? "";

/** Active security level of a notification, or "" (checkbox off / no level). */
export function securityLevel(items: Items): string {
  const on = firstItem(items, "EmailSecuritySettings");
  return on && on !== "0" ? firstItem(items, "EmailSigningCrypting") : "";
}

/** Items with the security block replaced — what Znuny's form would post. */
export function withSecurity(items: Items, sec: SecurityForm): Items {
  const out: Items = {};
  for (const [k, v] of Object.entries(items)) {
    if (!(SECURITY_ITEM_KEYS as readonly string[]).includes(k)) out[k] = v;
  }
  // Unchecked: Znuny's disabled selects are not posted, so no rows at all.
  if (!sec.enabled) return out;
  out.EmailSecuritySettings = ["1"];
  if (sec.level) out.EmailSigningCrypting = [sec.level];
  out.EmailMissingSigningKeys = [sec.missingSign || "Skip"];
  out.EmailMissingCryptingKeys = [sec.missingCrypt || "Skip"];
  return out;
}
