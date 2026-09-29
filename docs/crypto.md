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
(foundation + key management) is done. Inbound MIME-tree handling (B2),
signed/encrypted SMTP sending (B3) and customer keys / notifications (B4)
follow.

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
| — | CA bundle for inbound signature chain checks; without it a valid signature is `signed_untrusted` | `TIQORA_CRYPTO_SMIME_CA_PATH` |

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
`delete_private`, `relation_add`, `relation_delete`, `migrate`, `rehash`),
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

## Inbound: postmaster wiring

`tiqora.channels.email.pipeline.process_message()` resolves the crypto
config (SysConfig + env) and:

1. Runs `tiqora.crypto.inbound.process_inbound_crypto(raw, settings,
   config)` — a no-op unless PGP or S/MIME is enabled.
2. If a PGP-encrypted inline block (`-----BEGIN PGP MESSAGE-----...`) is
   found and decrypts successfully (passphrases from
   `PGP::Key::Password`), the plaintext replaces the article body.
3. Records the outcome as an `article_flag` row `TiqoraCryptoVerify =
   "<method>:<status>"` (e.g. `pgp:decrypted_verified`, `pgp:verify_failed`,
   `smime:verified`, `smime:signed_untrusted`).
4. A decrypt/verify failure **never blocks delivery** (like Znuny's
   `ArticleCheck::PGP`/`::SMIME`).

**Until B2**: only inline PGP and whole-message S/MIME verification;
PGP/MIME, S/MIME decryption and attachment-preserving MIME-tree handling
follow in B2.

## Outbound: GenericInterface `EmailSecurity`

Mirrors Znuny's `EmailSecurity` block on `TicketCreate` articles:

```json
{
  "Article": {
    "EmailSecurity": {
      "Backend": "PGP",
      "SignKey": "81877F5E",
      "EncryptKeys": ["81877F5E", "3b630c80"]
    }
  }
}
```

`op_ticket_create` calls `tiqora.crypto.outbound.apply_email_security()`,
which signs and/or encrypts the stored article body (`EncryptKeys` wins over
`SignKey`). For S/MIME, `SignKey`/`EncryptKeys` are store file names
(`<hash>.<n>`) or email addresses (first valid certificate/key for that
address); the private key's `.P` secret is passed to openssl via the
environment, never argv. Disabled backends or crypto failures leave the body
untouched (logged). Real signed/encrypted SMTP sending follows in B3.

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
  `test_crypto_postmaster.py` — EmailSecurity, audit row, postmaster flag.
- Frontend: `PgpKeysPage.test.tsx`, `SmimePage.test.tsx`,
  `QueuesPage.test.tsx`, `SystemInfoPage.test.tsx`.

Fixtures (`tests/_smime_fixtures.py`) build throwaway CA and leaf
certificates with `cryptography`.
