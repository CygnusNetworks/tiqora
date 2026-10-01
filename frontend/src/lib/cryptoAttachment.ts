import type { AttachmentMetaOut } from "@/lib/api";

/** PGP / S-MIME side files (signature.asc, smime.p7s, public keys,
 * PGPexch.htm) as the backend classifies them — see
 * tiqora.crypto.attachment_kind. */
export type CryptoKind = NonNullable<AttachmentMetaOut["crypto_kind"]>;

export type CryptoAtt = AttachmentMetaOut & { crypto_kind: CryptoKind };

/** Short chip text, same register as the file-type chips ("PDF", "DOC"). */
export const CRYPTO_CHIP: Record<CryptoKind, string> = {
  pgp_public_key: "KEY",
  pgp_signature: "SIG",
  pgp_signed_message: "PGP",
  pgp_message: "PGP",
  pgp_html_body: "HTML",
  smime_signature: "S/MIME",
  x509_certificate: "CERT",
};

/** Keys first (actionable), then the body duplicate, then the rest. */
export const CRYPTO_ORDER: Record<CryptoKind, number> = {
  pgp_public_key: 0,
  x509_certificate: 1,
  pgp_html_body: 2,
  pgp_message: 3,
  pgp_signed_message: 4,
  pgp_signature: 5,
  smime_signature: 6,
};

export function isCryptoAttachment(a: AttachmentMetaOut): a is CryptoAtt {
  return a.crypto_kind != null;
}

/** gpg's own layout: 4-hex blocks, a wider gap after the 5th of 10. */
export function formatFingerprint(fp: string): string {
  const blocks = fp.replace(/\s+/g, "").toUpperCase().match(/.{1,4}/g) ?? [];
  if (blocks.length === 10) {
    return `${blocks.slice(0, 5).join(" ")}  ${blocks.slice(5).join(" ")}`;
  }
  return blocks.join(" ");
}
