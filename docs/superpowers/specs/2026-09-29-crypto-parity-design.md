# PGP + S/MIME parity with Znuny 6.5 (B1–B4)

Date: 2026-09-29 · Status: approved direction (user: "alles, möglichst wenig fragen")

## Goal

Everything Znuny 6.5 offers for PGP and S/MIME works in Tiqora: key
management, reading signed/encrypted mail, sending signed/encrypted mail,
customer keys and signed/encrypted notifications. Keys and settings are
**shared with Znuny** during parallel operation (same on-disk formats, same
SysConfig/DB tables), so a migrated installation keeps its keys.

## Current state (2026-09-29)

Engines exist (`crypto/pgp.py`, `crypto/smime.py`), inbound handles inline
PGP and whole-message S/MIME verify, results land in `article_flag
TiqoraCryptoVerify` but nothing displays them. No UI, no outbound crypto on
SMTP, flat Tiqora-only S/MIME store, no passphrases, production image lacks
gpg and python-gnupg.

## Key decisions

- **Znuny-compatible key stores.** S/MIME uses Znuny's layout: certificates
  in `SMIME::CertPath` as `<subject_hash>.<n>`, private keys in
  `SMIME::PrivatePath` as `<subject_hash>.<n>` with the secret in
  `<subject_hash>.<n>.P`, signer relations in the Znuny table
  `smime_signer_cert_relations`. PGP uses the keyring from `PGP::Options`
  `--homedir` (falls back to `TIQORA_CRYPTO_PGP_GNUPGHOME`), passphrases
  from SysConfig `PGP::Key::Password` (key id → passphrase). Paths and
  on/off switches read SysConfig (`PGP`, `SMIME`, `SMIME::Bin`, `PGP::Bin`,
  `SMIME::CertPath`, `SMIME::PrivatePath`, `SMIME::CacheTTL` ignored) with
  `TIQORA_CRYPTO_*` env vars as overrides for container paths. The existing
  flat `<email>.crt/.key` store gets a one-off `tiqora crypto
  migrate-flat-store` CLI and is then removed.
- **Container**: runtime image installs `gnupg` and `openssl` explicitly and
  syncs `--extra crypto`; compose docs mount the key directories (read-write)
  so they can be shared with Znuny.
- **Security result model** (article API): `security: {method: pgp|smime,
  signed: bool, encrypted: bool, status: verified|signed_untrusted|
  verify_failed|decrypted|decrypt_failed|..., signer?: str, key_id?: str,
  detail?: str}` derived from `TiqoraCryptoVerify` flags.

## B1 — Foundation + key management

Backend:
- Dockerfile/compose changes above; startup self-check logs whether gpg /
  openssl are usable when a backend is enabled; admin system info shows it.
- `PgpEngine`: passphrase support, `PGP::Options` honoured (trust model),
  key listing with parsed metadata (key id, fingerprint, uids/emails,
  created, expires, status `good|expired|revoked`, has secret), delete
  public/secret key, export armored key.
- `SmimeEngine` + new `SmimeStore` (Znuny layout): add certificate (hash
  naming, collision index `.n`), add private key + secret (must match a
  cert modulus), list with parsed metadata (subject, issuer, emails, serial,
  fingerprint, not_before/after, status valid/expired, has private), delete,
  download, signer relations CRUD (table `smime_signer_cert_relations`),
  "related certificates" lookup, re-hash on add/delete.
- Admin API `/api/v1/admin/crypto-keys`: `GET /pgp`, `POST /pgp` (upload
  armored), `DELETE /pgp/{key_id}?secret=`, `GET /pgp/{key_id}/export`;
  `GET /smime`, `POST /smime/certificates`, `POST /smime/private-keys`
  (key + secret), `DELETE /smime/{filename}`, `GET /smime/{filename}`
  (download), `GET/POST/DELETE /smime/{filename}/relations`. Existing
  import endpoints stay as aliases. Every mutation writes `tiqora_crypto_key`
  audit rows. Secrets are never returned.
- Queue default sign key: validated picker values in Znuny format
  (`PGP::Detached::<keyid>`, `PGP::Inline::<keyid>`, `SMIME::Detached::<file>`)
  with `GET /api/v1/admin/crypto-keys/sign-key-options`.

Frontend:
- Admin pages **PGP** and **S/MIME** (list with status/expiry badges,
  upload, delete with confirm, download, details incl. fingerprint;
  S/MIME: private key upload with secret, signer relations dialog).
- Queue admin: default sign key becomes a select fed by sign-key-options.

## B2 — Inbound

- New `crypto/mime_walk.py`: walks the parsed message tree and handles
  - PGP/MIME `multipart/encrypted` (RFC 3156): decrypt, re-parse the inner
    entity, replace body **and attachments** of the article;
  - PGP/MIME `multipart/signed` detached verify (signature over the exact
    raw bytes of the signed part);
  - inline PGP (existing behaviour, kept);
  - S/MIME `multipart/signed` + `application/(x-)pkcs7-signature` detached
    verify and opaque `application/pkcs7-mime; smime-type=signed-data`;
  - S/MIME `enveloped-data` decrypt with the private keys whose certificate
    email matches one of the message's recipient addresses (To/Cc/
    Delivered-To), trying each (Znuny behaviour); decrypted entity re-parsed
    so attachments survive; a signed-inside-encrypted payload is verified
    afterwards.
  CA handling: signer relations + CertPath act as the trust store;
  `SMIME::NoVerify` semantics → `signed_untrusted` instead of failure.
- Decrypted content is stored in clear in the article (as Znuny's
  ArticleCheck does via ArticleUpdate); the original raw mail stays in
  `article_data_mime_plain`.
- Failures never block delivery; result flags as today with the richer
  status set.
- **Legacy/Znuny articles**: on article read, if an article still holds an
  encrypted body (Znuny articles that were never opened) and the backend
  is enabled, decrypt on view (read-only, result cached in the flag).
- Article API exposes `security`; ticket view shows a compact badge in the
  article header (🔒 encrypted, ✓ signed-verified, ⚠ untrusted/failed) with
  a popover (method, signer, key id, detail). Badge lives in the article
  header only; timeline/conversation layout is unchanged.

## B3 — Outbound

- New `crypto/mime_build.py`: builds real messages
  - PGP/MIME detached signature (`multipart/signed; protocol=
    "application/pgp-signature"; micalg=pgp-<hash>`), PGP/MIME encryption
    (`multipart/encrypted`), sign+encrypt; PGP inline (clear-signed /
    armored body) for `PGP::Inline`;
  - S/MIME detached signature (`multipart/signed; protocol=
    "application/pkcs7-signature"`), S/MIME encryption
    (`application/pkcs7-mime; smime-type=enveloped-data`), sign then encrypt;
  applied in the SMTP send path (`outbound_reply` / `build_message`) after
  the normal MIME message (body, attachments, headers) is built and before
  sending. The stored article keeps the clear content; the sent raw message
  (signed/encrypted) is stored as the article's plain source as Znuny does.
- Article/ticket send API gains `email_security: {backend: pgp|smime,
  method: inline|detached, sign_key?: str, encrypt: bool, encrypt_keys?:
  list[str]}`; encrypt keys default to the valid keys found for every
  recipient; request fails with 422 listing recipients without a usable key
  or an expired/revoked key.
- `crypto/outbound.py` (GenericInterface `EmailSecurity`) reuses the same
  builder, works on TicketCreate **and** TicketUpdate, and only rewrites the
  stored body when no SMTP send happens.
- `GET /api/v1/tickets/{id}/crypto-options?to=…&cc=…&queue_id=…` returns
  available sign keys (queue default preselected), per-recipient encrypt
  key status, and warnings (expired, revoked, missing).
- Compose UI (Reply, Forward, new email ticket, email outbound): a
  "Sicherheit" control next to the send button — *Keine / Signieren /
  Verschlüsseln / Signieren + verschlüsseln*, backend+method select,
  sign-key select; recipients without keys marked inline; send blocked with
  a clear message when encryption is chosen and a key is missing. Default:
  sign with the queue default sign key if configured, otherwise none.

## B4 — Customer keys + notifications

- Customer keys: PGP/S-MIME key upload on the agent customer detail page and
  in customer-user admin (stored in the shared key stores, linked to the
  customer email); customer portal gets a **preferences page** (language,
  password, PGP/S-MIME key upload) — this also closes the open "portal
  preferences" gap.
- `SMIMEFetchFromCustomer`: when enabled, the postmaster pre-step and a
  daily worker job import certificates from the customer backend attribute
  (`UserSMIMECertificate`) for customers with email traffic; renew replaces
  expired ones.
- Notifications: per notification event `EmailSigningCrypting`
  (`no`, `PGP sign only`, `PGP encrypt only`, `PGP sign and encrypt`,
  S/MIME equivalents) and `EmailMissingSigningKeys` /
  `EmailMissingCryptingKeys` (`Skip`, `Send unsigned/unencrypted`) read from
  the Znuny `notification_event_item` rows; signing key falls back to the
  queue default sign key, then the system address. Admin notification editor
  gains these fields.

## Error handling

Crypto failures on receive never block ingest; on send they block the send
with a 422/502 describing the problem (no silent unencrypted send when
encryption was requested). Missing binaries → backend reported unavailable
in admin, operations return `unavailable`.

## Testing

Real gpg/openssl in tests (skip if absent locally, required in CI and the
image). Fixtures: generated PGP keys and self-signed S/MIME CA + leaf certs;
round-trip tests (build → parse → verify/decrypt) for every MIME shape;
Thunderbird/Outlook-style sample mails in `tests/fixtures/crypto/` for
inbound; Znuny-layout store tests (hash naming, collision, relations);
API tests for key admin, crypto-options, send with/without keys; frontend
vitest for admin pages, badge, compose security control. Regenerate the
OpenAPI snapshots after API changes.
