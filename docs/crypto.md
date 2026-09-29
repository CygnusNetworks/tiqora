# PGP & S/MIME

`backend/src/tiqora/crypto/` ports Znuny's `Kernel::System::Crypt::PGP` and
`Kernel::System::Crypt::SMIME`: key management, verify and decrypt inbound
email articles, sign and encrypt outbound ones. Keys and settings are
**shared with Znuny**: the same gpg keyring, the same S/MIME directory
layout, the same SysConfig settings and DB tables. A directory pair can be
used by a running Znuny and Tiqora at the same time.

Both backends are **off by default** (Znuny's `PGP` / `SMIME` SysConfig
switches) and need their external tool (`gpg` / `openssl`; both ship in the
Docker image).

Status of the parity work
([design](superpowers/specs/2026-09-29-crypto-parity-design.md)): B1
(foundation + key management), B2 (inbound MIME-tree handling, article
security status, decrypt-on-view), B3 (signed/encrypted sending) and of B4
the customer keys and `SMIME::FetchFromCustomer` are done.
Signed/encrypted notifications (rest of B4) follow.

## Configuration

Tiqora reads Znuny's SysConfig (DB-backed, `sysconfig_modified` over
`sysconfig_default`). `TIQORA_CRYPTO_*` env vars override a setting when set
— mainly for container paths, where Znuny's host paths do not exist.

| SysConfig | Meaning | Env override |
|---|---|---|
| `PGP` | PGP on/off | `TIQORA_CRYPTO_PGP_ENABLED` |
| `PGP::Bin` | gpg binary; ignored if the path does not exist here → `gpg` on `PATH` | `TIQORA_CRYPTO_GPG_BIN` |
| `PGP::Options` | `--homedir <dir>` = the keyring; remaining options (e.g. `--trust-model always`) are passed to gpg | `TIQORA_CRYPTO_PGP_GNUPGHOME` (homedir only) |
| `PGP::Key::Password` | key id → passphrase, used for signing, decrypting and deleting secret keys | — |
| `PGP::Options::DigestPreference` | signature digest (`--personal-digest-preferences`) | — |
| `SMIME` | S/MIME on/off | `TIQORA_CRYPTO_SMIME_ENABLED` |
| `SMIME::Bin` | openssl binary; same fallback as `PGP::Bin` | `TIQORA_CRYPTO_OPENSSL_BIN` |
| `SMIME::CertPath` | certificate directory | `TIQORA_CRYPTO_SMIME_CERT_DIR` |
| `SMIME::PrivatePath` | private key directory | `TIQORA_CRYPTO_SMIME_PRIVATE_DIR` |
| `SMIME::CacheTTL` | ignored (Tiqora reads the files directly) | — |
| — | extra CA bundle (`-CAfile`) for inbound signature chain checks, on top of `SMIME::CertPath` | `TIQORA_CRYPTO_SMIME_CA_PATH` |

Znuny ships `SMIME::CertPath`/`SMIME::PrivatePath` as *invalid* (inactive)
settings; they only count once enabled in Znuny's SysConfig — or set the env
vars. Remember that values in Znuny's `Config.pm` bypass the SysConfig DB
and are invisible to Tiqora; mirror them into SysConfig.

`PGP::Key::Password` keys are matched case-insensitively against every id of
a key: fingerprint, long/short primary id, long/short subkey ids. Znuny's UI
shows (and its passwords are usually keyed by) the **last subkey's** short
id for secret keys — Tiqora calls that `znuny_key_id`.

`tiqora crypto status`, the admin pages and **System info** show the
resolved state (switch, binary version, directories, problems); the API
logs `crypto_backend_ready` / `crypto_backend_unusable` at startup for every
enabled backend. Deployment (volumes, permissions):
[deploy/docker-compose.md](deploy/docker-compose.md#pgp--smime-key-stores-shared-with-znuny).

## Key stores

### PGP

The gpg keyring from `PGP::Options --homedir`. gpg is authoritative for
"which keys exist"; `PgpEngine` (`pgp.py`, via `python-gnupg`) lists keys
with parsed metadata (fingerprint, key ids, uids/emails, created, expires,
`good|expired|revoked`, secret present, algorithm/bits, subkeys), imports
armored keys, deletes the secret part only or the whole key, and exports the
**public** key. Secret keys are never exported through Tiqora.

### S/MIME (Znuny layout)

`SmimeStore` (`smime_store.py`) writes exactly what `SMIME.pm` writes:

- certificate: `SMIME::CertPath/<subject_hash>.<n>` (PEM). `subject_hash` is
  `openssl x509 -subject_hash` (the hash `-CApath` lookups use); `n` is the
  first free index 0–99 among certificates with the same hash. Uploading a
  certificate that is already stored (same SHA-1 fingerprint) is refused.
- private key: `SMIME::PrivatePath/<cert filename>` (PEM, normally
  encrypted), secret in `<cert filename>.P`, both mode `0600`. The key is
  matched to exactly one stored certificate by public key, so the certificate
  must be uploaded first.
- a key uploaded **without** a secret is accepted only when unencrypted; it
  is then stored AES-256-encrypted under a generated secret, because Znuny
  requires one.
- DER certificates are converted to PEM.

`smime_index.py` keeps Znuny's DB side in sync: one `smime_keys` row per
certificate (`key_type = 'cert'`) and private key (`'P'`) with hash, file
name, sorted comma-joined emails, expiry date, SHA-1 fingerprint and Znuny's
subject string; signer relations in `smime_signer_cert_relations`
(identified by fingerprints).

Two deliberate differences from Znuny:

- After a delete the remaining `<hash>.<n>` files of that hash are
  renumbered without gaps (OpenSSL's `-CApath` lookup stops at the first
  missing index). `smime_keys.file_name` and customer `SMIMEFilename`
  preferences follow the rename.
- Deleting a certificate also clears the customers' SMIME preferences
  pointing at it (Znuny's AdminSMIME does this in the UI module).

`tiqora crypto smime-rehash` renames files whose hash prefix does not match
the current openssl hash (Znuny `_ReHashCertificates`) and re-syncs
`smime_keys` with the directory (`ReIndexCertificate`/`ReIndexPrivate`).

### Audit trail

`tiqora_crypto_key` records one row per mutation — `action` (`import`,
`delete`, `delete_secret`, `add_certificate`, `add_private`,
`delete_private`, `relation_add`, `relation_delete`, `migrate`, `rehash`,
`customer_link`; customer uploads carry `detail` = `customer <login>` and
`user_id` NULL when the customer uploaded through the portal),
`user_id`, `identifier` (PGP fingerprint / S/MIME file name), `email`,
`detail`. Never key material or secrets.

## Admin UI and API

Admin pages **PGP keys** (`/admin/pgp`) and **S/MIME certificates**
(`/admin/smime`), both under *Communication*: list with status/expiry badges,
upload, details (fingerprint etc.), download, delete with confirmation;
S/MIME additionally private key upload with secret and a signer-relations
dialog. Keys can be managed while a backend is switched off (the page says
so) — signing/encryption only happens once it is enabled.

The queue form's **default sign key** is a select fed by the sign-key
options; the value is stored in Znuny's format (`PGP::Detached::<id>`,
`PGP::Inline::<id>`, `SMIME::Detached::<hash>.<n>`) and validated on save
(format, and the key must exist with its secret part). A stored value Tiqora
cannot see stays selectable so editing other fields does not drop it.

`/api/v1/admin/crypto-keys` (admin only):

| Endpoint | |
|---|---|
| `GET ""` | audit trail (newest 500) |
| `GET /status` | per-backend self-check |
| `GET /pgp`, `POST /pgp` `{ascii_armor}` | list / import |
| `DELETE /pgp/{key_id}?secret=` | delete the secret key only, or the whole key |
| `GET /pgp/{key_id}/export` | armored public key |
| `GET /smime` | certificates incl. private-key flag, CA flag, status |
| `POST /smime/certificates` `{certificate}` | PEM text or base64 DER |
| `POST /smime/private-keys` `{private_key, secret}` | |
| `DELETE /smime/{filename}?private_only=` | returns the renumbering map |
| `GET /smime/{filename}` | certificate PEM |
| `GET/POST/DELETE /smime/{filename}/relations` | signer relations (`POST {ca_filename}`, `DELETE ?ca_fingerprint=`) |
| `GET /sign-key-options?queue_id=&email=` | default-sign-key values; `queue_id` filters by the queue's system address like Znuny's AdminQueue |
| `POST /pgp-import`, `POST /smime-register` | older aliases, kept for API clients |

Errors: `503` backend not configured / binary missing, `404` unknown key,
`422` refused (duplicate, wrong secret, no matching certificate, …).

## Customer keys

Port of Znuny's customer preference modules `PGP` / `SMIME`
(`Kernel::Output::HTML::Preferences::PGP`/`::SMIME`), which Znuny shows in
the customer interface (`CustomerPreferencesGroups###PGP`/`###SMIME`) and
in `AdminCustomerUser` (`crypto/customer_keys.py`):

- The upload goes into the **shared** store (keyring / `SMIME::CertPath`,
  `smime_keys` row) and the customer's `customer_preferences` get the keys
  Znuny writes: `PGPKeyID` (long key id) and `PGPFilename`
  (`<uid>-<bits>-<key id>.pub`); `SMIMEHash`, `SMIMEFingerprint`,
  `SMIMEFilename` (`<hash>.<n>`). Encryption looks keys up by recipient
  address, as in Znuny; the preferences record the upload.
- A customer's keys are the store entries carrying the customer's email
  address, plus the one the preferences point at.
- S/MIME uploads may be PEM, DER, PKCS#7 (`.p7b`) or PKCS#12 without
  password (Znuny `ConvertCertFormat`); the end-entity certificate is
  stored. A certificate that is already stored is linked instead of refused.
- Tighter than Znuny (which takes anything): public material only (no
  `PRIVATE KEY BLOCK`, no CA certificates); **portal** uploads must carry the
  customer's own email address, so nobody can plant a key for someone
  else's address. Agents may link a key for another address of the customer.
- Delete (agents only) removes the key from the store and clears the
  preferences; keys with a secret part / private key are refused there and
  stay with the admin pages.

Where:

| Surface | Endpoint | Rights |
|---|---|---|
| agent customer page (card *Encryption keys*), customer-user admin (row action *Keys*) | `GET/POST/DELETE /api/v1/customers/{login}/crypto-keys[/pgp\|/smime[/{id}]]` | read: any agent; write: `rw` in `admin` or `users` (Znuny `Frontend::Module###AdminCustomerUser` groups) |
| portal `/portal/preferences` | `GET /api/portal/preferences`, `POST …/pgp-key`, `POST …/smime-certificate` | the logged-in customer, own keys only, upload only |

Both only exist while the backend is enabled (Znuny's `Param()` returns
nothing otherwise): the agent card is hidden, uploads answer `409`, the portal
section is hidden and its endpoints answer `404` — also when the preference
group is deactivated in SysConfig.

### SMIME::FetchFromCustomer

With `SMIME` and `SMIME::FetchFromCustomer` on (default off), certificates
come from the customer backend attribute `UserSMIMECertificate`
(`crypto/customer_fetch.py`, Znuny `Crypt::SMIME::FetchFromCustomer`):

- **Postmaster**: before inbound crypto, the `From` address of every mail is
  looked up (only addresses of valid `customer_user` rows, Znuny's
  `PostMasterSearch`) and new certificates are added to `SMIME::CertPath`, so
  the signature check already sees them. Skipped when
  `PostMaster::PreFilterModule###000-SMIMEFetchFromCustomer` is deactivated.
  Never blocks delivery.
- **Daily** (`smime_customer_renew` worker loop, 02:02 UTC, flag
  `daemon.smime_customer_renew.enabled`, default on like Znuny's
  `RenewCustomerSMIMECertificates` task): for every address of a stored
  certificate the customer's current backend certificate is added if it is
  not stored yet. Like Znuny, old certificates are not deleted; running next
  to Znuny's task is harmless (duplicates are skipped).

Tiqora's customer data is the `customer_user` table, so the attribute is
read from:

| Source | Settings |
|---|---|
| customer LDAP directory | `TIQORA_CUSTOMER_LDAP_ENABLED`/`_HOST`/`_BASE_DN`/`_BIND_*`/`_ALWAYS_FILTER`; entry found by `TIQORA_CUSTOMER_LDAP_EMAIL_ATTR` (`mail`), attribute `TIQORA_CUSTOMER_LDAP_SMIME_ATTR` (`userSMIMECertificate`, also `;binary`) |
| a `customer_user` column (Znuny DB backend map) | `TIQORA_CUSTOMER_SMIME_CERT_COLUMN` (PEM, base64 or binary) |

Values may be PEM, DER, PKCS#7 or PKCS#12 without password.

## CLI

```
tiqora crypto status
tiqora crypto pgp-import <key.asc> [--email x@example.org] [--purpose sign|encrypt|both]
tiqora crypto pgp-list
tiqora crypto pgp-delete <key-id> [--secret-only]
tiqora crypto smime-list
tiqora crypto smime-add-cert <cert.pem|cert.der>
tiqora crypto smime-add-key <key.pem> [--secret <passphrase>]
tiqora crypto smime-rehash
tiqora crypto migrate-flat-store [--from-cert-dir D] [--from-private-dir D] [--secret S] [--keep] [--dry-run]
```

### Migrating the old flat store

Before B1 Tiqora kept S/MIME material as `<email>.crt` / `<email>.key` in
`TIQORA_CRYPTO_SMIME_CERT_DIR` / `TIQORA_CRYPTO_SMIME_PRIVATE_DIR`. That
layout is gone; run once:

```
tiqora crypto migrate-flat-store --dry-run   # show what would move
tiqora crypto migrate-flat-store             # add to the Znuny layout, delete the flat files
```

It works in place (the same directories now hold the Znuny layout),
imports each certificate and its key (unencrypted keys get a generated
secret; pass `--secret` for encrypted ones), indexes both in `smime_keys`
and deletes the flat files once both parts are stored (`--keep` keeps them).
Certificates already present are skipped, so it is safe to re-run.

## Inbound: reading signed and encrypted mail

### At ingest

`tiqora.channels.email.pipeline.process_message()` resolves the crypto
config and runs `tiqora.crypto.inbound.process_inbound_crypto()` — a no-op
unless PGP or S/MIME is enabled. It hands the raw mail to
`tiqora.crypto.mime_walk.walk_message()`, which walks the MIME tree on the
**raw bytes** (CRLF canonical, never re-serialised before a signature check):

| Shape | Handling |
|---|---|
| PGP/MIME `multipart/encrypted` (RFC 3156) | second part decrypted; the inner entity replaces the node and is walked again, so body **and attachments** survive; a Thunderbird `protected-headers` subject replaces a placeholder outer subject (`...`) |
| PGP/MIME `multipart/signed` | detached signature checked over the exact bytes of the first part; the signature part is dropped |
| inline PGP | `BEGIN PGP MESSAGE` in a text part decrypted, `BEGIN PGP SIGNED MESSAGE` verified and shown without armor; `*.pgp`/`*.gpg` (and armored `*.asc`) attachments decrypted and renamed (`report.pdf.pgp` → `report.pdf`, as Znuny does) |
| S/MIME `multipart/signed` (`application/(x-)pkcs7-signature`) | `openssl smime -verify` on the entity; the `smime.p7s` part is dropped |
| S/MIME opaque `application/(x-)pkcs7-mime; smime-type=signed-data` | verified, the signed content extracted and walked |
| S/MIME `enveloped-data` (or `pkcs7-mime` without `smime-type`) | decrypted with every private key whose certificate carries a recipient address (`Resent-To`, `Envelope-To`, `To`, `Cc`, `Delivered-To`, `X-Original-To`) until one works — Znuny's `PrivateSearch` loop; the decrypted entity is walked again (signed inside encrypted) |

The rewritten content replaces body and attachments before the article is
written, so the article holds the mail **in clear** (Znuny's ArticleCheck
does the same with `ArticleUpdate` when an agent first opens it); the
original mail is kept in `article_data_mime_plain`. Nothing ever blocks
delivery: a layer that cannot be processed stays as it is (e.g. the
`smime.p7m` attachment) and is reported in the status.

### Trust

- **S/MIME**: `SMIME::CertPath` is the trust store (`-CApath`; its
  `<subject_hash>.<n>` names are exactly what `-CApath` looks up), plus
  `TIQORA_CRYPTO_SMIME_CA_PATH` and openssl's default store, like Znuny's
  call. CAs linked through signer relations are in CertPath, so they are
  trust anchors automatically; a customer's self-signed certificate
  uploaded to CertPath is trusted too. A signature that is cryptographically
  valid but whose chain does not validate is `signed_untrusted` — Znuny's
  `SMIME::NoVerify` retry, but never shown as verified (Tiqora always
  retries; the setting itself is not read).
- **PGP**: a good signature from a key in the keyring is `verified`
  (Znuny's `GOODSIG` rule, regardless of ownertrust); an expired/revoked key
  or ownertrust *never* gives `signed_untrusted`, a signer missing from the
  keyring `unknown_key`.
- **Sender check** (RFC 3850 §3, Znuny bug#5098): a verified signature whose
  signer addresses do not include the `From`/`Sender` address becomes
  `signed_untrusted`, for both backends.

### Status model

The article API returns `security` on `GET /tickets/{id}/articles` and
`GET /tickets/{id}/articles/{article_id}/body`:

```json
{"method": "pgp", "signed": true, "encrypted": true, "status": "verified",
 "signer": "Carla Customer <customer@example.com>",
 "key_id": "3F2A…", "detail": "good signature"}
```

`status` is the worst layer: `decrypt_failed` > `verify_failed` > `error`
> `unavailable` > `unknown_key` > `signed_untrusted` > `verified` >
`decrypted` (so a signed+encrypted mail reports its signature). `key_id` is
the PGP fingerprint, the S/MIME signer's SHA-1 fingerprint, or — for
encrypted-only S/MIME — the key file used to decrypt.

Stored in Znuny's `article_flag` (values are `VARCHAR(50)`, so split):
`TiqoraCryptoVerify` = `<method>:<status>` (pre-B2 format, older
`decrypted_verified`/`decrypted_unverified` values are still read),
`TiqoraCryptoLayers` (`signed`, `encrypted`, `signed,encrypted`),
`TiqoraCryptoSigner`, `TiqoraCryptoKeyID`, `TiqoraCryptoDetail` (truncated).
GDPR anonymisation removes `TiqoraCryptoSigner`.

### Legacy Znuny articles: decrypt on view

Znuny decrypts only when an agent opens an article, so a migrated database
holds never-opened articles that are still encrypted (inline PGP body,
`smime.p7m`, `encrypted.asc`). For those the read endpoints (`/body`,
`/attachments`, attachment download incl. `cid:` lookups) show the
decrypted content **read-only** — the stored article is not changed, so
Znuny keeps reading it as before. Source is the raw mail in
`article_data_mime_plain` (or the inline-PGP body when there is none);
decrypted attachments get virtual negative ids (`-1` = first). The security
result is cached in the flags (from then on the article list shows it), the
decrypted content in-process for 10 minutes (also failures, so newly
uploaded keys take effect after that). Legacy articles that are only
*signed* get their flags computed once the same way.

### Ticket view

The article header shows a badge — 🔒 encrypted, ✓ signature verified,
⚠ untrusted / unknown key / invalid / not decrypted — with a popover
(method, protection, signer, key id, detail): full badge in the reader
(split view), glyph in the conversation bubble header, a non-interactive
marker in the timeline's collapsible header.

## Outbound: signed and encrypted sending

### What is sent

`tiqora.crypto.mime_build` (port of the `EmailSecurity` block of Znuny's
`Kernel::System::Email::Send`) takes the normal outgoing message — built by
`smtp.build_message` with all headers, body and attachments — and wraps its
content:

| Backend / method | Shape |
|---|---|
| PGP detached (default) | sign: `multipart/signed; micalg=pgp-<hash>; protocol="application/pgp-signature"` (RFC 3156 §5), `micalg` taken from the digest gpg actually used; encrypt: `multipart/encrypted; protocol="application/pgp-encrypted"` with the `Version: 1` part and `encrypted.asc`; sign + encrypt: the signed entity inside the encrypted one (RFC 3156 §6.1, as Znuny) |
| PGP inline (`PGP::Inline::<id>`) | clear-signed text body, or an armored encrypted (and signed) body; on encryption every attachment becomes `<name>.pgp`. HTML bodies are sent as plain text (inline PGP is a text format; Znuny only offers it without rich text) |
| S/MIME (always detached) | sign: `multipart/signed; protocol="application/pkcs7-signature"; micalg=sha-256` with `smime.p7s` (signer-relation CAs included, Znuny `-certfile`); encrypt: `application/pkcs7-mime; smime-type=enveloped-data` (AES-256) `smime.p7m`; sign + encrypt: signed entity inside the envelope |

Only the content moves into the signed/encrypted entity; `From`, `To`,
`Subject`, `Message-ID`, threading headers etc. stay outside. The signed part
is canonical CRLF and 7-bit clean (text quoted-printable, which also protects
trailing whitespace like the `-- ` signature delimiter; binaries base64), and
the message is sent **as those exact bytes** (`SmtpMailSender` sends the raw
form; `Bcc` is only in the envelope) — re-serialising could re-fold a header
and break the signature. Encryption is only for the recipients' keys
(To/Cc/Bcc), not for the sender, like Znuny.

### Where

Every agent email goes through `channels.email.outbound_reply.
deliver_agent_email_reply`: **reply**, **forward** and the email of a **new
email ticket** (`POST /tickets` + `POST /tickets/{id}/articles`), plus
GenericInterface `ArticleSend` (below). Bounce is excluded (it resends a
mail verbatim). Forward now really sends (send-then-store like a reply,
502 on SMTP failure) — before B3 it was only stored.

- keys are resolved and the message built **before** anything is sent or
  stored, so a key problem fails the request without side effects;
- the stored article keeps the **clear** content; the sent raw mail is kept
  in `article_data_mime_plain` (Znuny does the same) and the article gets
  `TiqoraCrypto*` flags (`verified` for a signature by our key, `decrypted`
  = "encrypted" for encryption only; detail `sent: …`), so the ticket view
  shows the badge on sent articles too;
- with outbound SMTP disabled the article is stored the same way (queued).

### API

`POST /tickets/{id}/articles` (agent email only; 422 on other channels) and
`POST /tickets/{id}/articles/{article_id}/forward` take

```json
"email_security": {"backend": "pgp", "method": "detached",
                   "sign_key": "81877F5E", "encrypt": true,
                   "encrypt_keys": null}
```

- `sign_key` — PGP key id/fingerprint (Znuny id as in the queue default) or
  S/MIME `<hash>.<n>` (also an email address); the Znuny form
  `PGP::Inline::<id>` is accepted and sets the method. The key needs its
  secret part and must be valid (not expired/revoked).
- `encrypt_keys` — optional explicit selection (PGP fingerprints/ids,
  S/MIME file names). Omitted: the first usable key of every To/Cc/Bcc
  recipient (Znuny's preselection). An explicit selection must cover every
  recipient (Znuny `_CheckRecipient`).
- Omitted/`null` `email_security` = plain mail. The server does **not**
  apply the queue default on its own (API clients, AI replies and process
  automation keep sending as before); the compose UI preselects it.

Errors: **422** `email_security: no usable PGP encryption key: a@x
(missing), b@y (expired)` — reasons `missing`, `expired`, `revoked`,
`no_selected_key`, `unknown_key`; also for a sign key without secret, a
disabled backend or encryption without recipients. **502** when gpg/openssl
fail while building. Never a silent unencrypted send.

`GET /tickets/{id}/crypto-options?to=&cc=&bcc=` (reply/forward, `ro` on the
ticket) and `GET /tickets/crypto-options?queue_id=&to=…` (new ticket,
`create` on the queue) return per enabled backend the sign keys for the
sender (the queue's system address; the queue default key is always listed),
the methods, each recipient's keys with status (`ok`, `missing`, `expired`,
`revoked`) and the preselected key, `can_encrypt`, and top-level `default`
(the queue's `default_sign_key` as an `email_security` object, or `null`) and
`warnings` (default key missing/expired, backend disabled). With both
backends off: `{"enabled": false}`.

### Compose UI

Reply, Forward and New ticket (email) show a **Sicherheit** row
(`EmailSecurityControl` + `useEmailSecurity`, a self-contained component):
*Keine / Signieren / Verschlüsseln / Signieren + verschlüsseln*, PGP or
S/MIME, PGP/MIME or inline PGP, the sign key; when encrypting each recipient
is shown with its key status. Default: sign with the queue default sign key
if configured, otherwise none. Sending is blocked with a message naming the
recipients without a usable key. Nothing is shown when PGP and S/MIME are
both disabled.

### GenericInterface `EmailSecurity`

```json
{"Article": {"ArticleSend": 1, "CommunicationChannel": "Email", "To": "…",
  "EmailSecurity": {"Backend": "PGP", "Method": "Detached",
                    "SignKey": "81877F5E", "EncryptKeys": ["81877F5E"]}}}
```

- `ArticleSend: 1` (TicketCreate **and** TicketUpdate, agents): the article is
  sent through the same path (`crypto.outbound.email_security_from_znuny`
  maps the hash); `To` is required; key problems → `…InvalidParameter`, send
  failures → `…OperationFailed`, nothing stored.
- Without `ArticleSend` (Znuny ignores `EmailSecurity` then) Tiqora keeps
  rewriting the **stored** body with the same builder: PGP as inline PGP
  (clear-signed / armored), S/MIME as the signed/encrypted MIME entity.
  Best effort — a disabled backend or failure leaves the body as it is.

## Tests

All crypto tests run against real `gpg`/`openssl` and skip when the binary is
missing (`GNUPGHOME` lives directly under `/tmp`: gpg-agent's socket path is
limited to ~104 bytes on macOS).

- `test_crypto_pgp.py`, `test_crypto_pgp_keys.py` — sign/verify/encrypt/
  decrypt; key listing metadata, expired status, Znuny ids, passphrases from
  `PGP::Key::Password`, digest preference, delete secret/whole key, export,
  `PGP::Options` parsing and pass-through.
- `test_crypto_smime.py`, `test_crypto_smime_store.py` — openssl roundtrips,
  encrypted keys with secret + signer CA; Znuny layout (hash naming vs.
  `openssl x509 -subject_hash`, collision index, `.P` secrets and modes, DER,
  generated secrets, compaction, rehash, path-traversal guard).
- `test_crypto_config.py` — SysConfig resolution and env overrides, status.
- `test_crypto_admin_api.py` (`db`) — admin API incl. `smime_keys` /
  relation rows as Znuny writes them, audit rows, sign-key options, queue
  validation, legacy alias, flat-store migration.
- `test_crypto_outbound.py`, `test_crypto_keystore.py`,
  `test_crypto_postmaster.py` — stored-body EmailSecurity, GI mapping, audit
  row, postmaster flag.
- `test_crypto_mime_build.py` — every outbound shape built with the support
  keys and read back with the customer's (inbound walk: verify/decrypt, body
  and attachments), plus the raw tools: `gpg --verify` on the extracted
  signed part (and `micalg` = the digest gpg reports), `openssl smime -verify`
  / `-decrypt` on the raw mail, `protocol`/`micalg` parameters, CRLF-only
  and 7-bit output, LF-converted-in-transit, tampering, Bcc not on the wire.
- `test_crypto_compose.py` — key choice: defaults per recipient, explicit
  selection coverage, missing/expired/unknown keys, sign key without secret,
  compose options with queue-default preselection and warnings.
- `test_crypto_outbound_send.py` (`db`) — through the API with a recording
  SMTP: reply PGP sign+encrypt, forward S/MIME sign+encrypt, GI
  `ArticleSend`, 422 without keys (nothing sent/stored), crypto-options.
- `test_crypto_mime_walk.py` — every inbound shape built by real gpg/openssl
  and read back: PGP/MIME signed (incl. LF-only mail, tampered, unknown key,
  sender mismatch), encrypted with attachments, sign+encrypt, signed inside
  encrypted, wrong recipient; inline PGP (encrypted, clear-signed, tampered,
  encrypted attachment); S/MIME detached (trusted via CertPath, untrusted,
  tampered), opaque, enveloped (To / Delivered-To / no key), signed inside
  encrypted; plus the committed sample mails.
- `test_crypto_inbound_articles.py` (`db`) — postmaster → article in clear,
  attachments, flags, raw mail kept, article API `security`; failures still
  deliver; legacy decrypt-on-view (virtual attachments, read-only, flag
  cache, inline PGP without raw mail).
- `test_crypto_customer_cert_format.py` — ConvertCertFormat (PEM, DER,
  PKCS#7 PEM/DER with chain, PKCS#12 with/without passphrase).
- `test_crypto_customer_keys.py` (`db`) — customer preferences as Znuny
  writes them, upload guards (secret keys, CA, foreign address), delete,
  FetchFromCustomer via a `customer_user` column and a mocked LDAP
  directory, deactivated pre-filter, renew + worker tick.
- `test_customer_keys_api.py` (`db`) — agent API incl. rights and disabled
  backends; portal preferences (language, keys, password change).
- `test_crypto_inbound_articles.py` — the fetch pre-step runs before inbound
  crypto and only when enabled.
- Frontend: `PgpKeysPage.test.tsx`, `SmimePage.test.tsx`,
  `QueuesPage.test.tsx`, `SystemInfoPage.test.tsx`,
  `ArticleSecurityBadge.test.tsx`, `EmailSecurityControl.test.tsx`,
  `CustomerDetailPage.test.tsx`, `CustomerUsersPage.test.tsx`, portal
  `PreferencesPage.test.tsx`.

Fixtures (`tests/_smime_fixtures.py`) build throwaway CA and leaf
certificates with `cryptography`; `tests/_crypto_mail_fixtures.py` builds
the mails (a customer and the support mailbox with PGP keys, S/MIME CA and
leaf certificates, a Znuny-layout store). `tests/fixtures/crypto/` holds
realistic samples — Thunderbird PGP/MIME (sign+encrypt, protected subject,
PDF attachment), Outlook S/MIME detached-signed and encrypted — with the
throwaway keys to read them (certificates valid 30 years); regenerate with
`cd backend && uv run python -m tests._crypto_mail_fixtures`. gitleaks
allowlists that directory.
