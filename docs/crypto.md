# PGP & S/MIME

`backend/src/tiqora/crypto/` ports the concepts of Znuny's
`Kernel::System::Crypt::PGP` and `Kernel::System::Crypt::SMIME`: verify and
decrypt inbound email articles, sign and encrypt outbound ones. Both PGP and
S/MIME are **OFF by default** and require their respective external tool.

## Modules

- `pgp.py` — `PgpEngine`, a thin wrapper around the `gpg` binary via
  `python-gnupg` (same tool Znuny's `PGP::Bin` shells out to). Sign, verify,
  encrypt, decrypt, import.
- `smime.py` — `SmimeEngine`, shells out to `openssl smime ...` directly
  (same as Znuny's `SMIME.pm`). The `cryptography` package — already a
  Tiqora dependency — has no public API for S/MIME verify/encrypt/decrypt,
  only signature *building*, so `openssl` is the pragmatic choice here.
- `keystore.py` — key lookup/import bookkeeping:
  - PGP keys live in the gpg keyring (`TIQORA_CRYPTO_PGP_GNUPGHOME`); gpg
    itself is authoritative. `import_pgp_key()` imports into the keyring
    *and* records an audit row.
  - S/MIME has no keyring: `SmimeKeyStore` is a Tiqora-owned convention —
    flat directories of `<email>.crt` / `<email>.key` files (email is
    lower-cased and path-sanitized). Deliberately simpler than Znuny's
    hash-indexed `SMIME::CertPath`/`SMIME::PrivatePath` OpenSSL certificate
    store layout.
- `inbound.py` — best-effort decrypt+verify wired into the postmaster
  pipeline (see below).
- `outbound.py` — honors the GenericInterface `EmailSecurity` param on
  `TicketCreate` articles (see below).
- `tiqora_crypto_key` table (Alembic `versions_tiqora`) — audit trail only
  (`key_type`, `identifier`, `email`, `purpose`, `has_private_key`,
  `created`); never the key material itself.

## Config keys (all default OFF/empty)

| Key | Purpose |
|---|---|
| `TIQORA_CRYPTO_PGP_ENABLED` | Master switch for PGP (inbound verify/decrypt + outbound EmailSecurity PGP) |
| `TIQORA_CRYPTO_PGP_GNUPGHOME` | GNUPGHOME directory for the Tiqora-owned keyring |
| `TIQORA_CRYPTO_SMIME_ENABLED` | Master switch for S/MIME |
| `TIQORA_CRYPTO_SMIME_CERT_DIR` | Directory of `<email>.crt` files |
| `TIQORA_CRYPTO_SMIME_PRIVATE_DIR` | Directory of `<email>.key` files |
| `TIQORA_CRYPTO_OPENSSL_BIN` | `openssl` binary path (default `"openssl"`, PATH lookup) |

## CLI

```
tiqora crypto pgp-import <key-file.asc> [--email x@example.com] [--purpose sign|encrypt|both]
tiqora crypto smime-register <email> [--cert-file cert.pem] [--key-file key.pem] [--purpose ...]
```

## Inbound: postmaster wiring

`tiqora.channels.email.pipeline.process_message()` (the entry point
`postmaster.process_account()` calls per message) now:

1. Runs `tiqora.crypto.inbound.process_inbound_crypto(raw, settings)` — a
   no-op unless `TIQORA_CRYPTO_PGP_ENABLED`/`TIQORA_CRYPTO_SMIME_ENABLED`
   are set.
2. If a PGP-encrypted inline block (`-----BEGIN PGP MESSAGE-----...`) is
   found and decrypts successfully, the decrypted plaintext replaces the
   article body (headers/routing still come from the original raw
   message).
3. Once the article id is known, records the outcome as an `article_flag`
   row: `article_key = "TiqoraCryptoVerify"`, `article_value =
   "<method>:<status>"` (e.g. `pgp:decrypted_verified`,
   `pgp:verify_failed`, `smime:verified`).
4. A decrypt/verify failure **never blocks delivery** — the article is
   still created with whatever body was parsed (mirrors Znuny's
   `ArticleCheck::PGP`/`::SMIME`, which annotate rather than reject).

**Simplification vs Znuny**: this detects/decrypts an inline PGP block
found anywhere in the raw message, and — for S/MIME — verifies a signed
message as a whole (`openssl smime -verify` parses the MIME structure
itself). It is not a full MIME-tree walk that decrypts/verifies individual
`multipart/encrypted`/`multipart/signed` sub-parts while preserving
sibling attachments. Inbound S/MIME **decrypt** (`-encrypt`d mail) is not
wired up at the postmaster level at all — it would need the *receiving*
mail account's own private key resolved by recipient address, which the
pipeline does not currently thread through; `SmimeEngine.decrypt()` exists
and is unit-tested, only the inbound wiring stops at verify-only.

## Outbound: GenericInterface `EmailSecurity`

Mirrors Znuny's `EmailSecurity` block on `TicketCreate` articles
(`Kernel::GenericInterface::Operation::Ticket::TicketCreate`):

```json
{
  "Article": {
    "...": "...",
    "EmailSecurity": {
      "Backend": "PGP",
      "SignKey": "81877F5E",
      "EncryptKeys": ["81877F5E", "3b630c80"]
    }
  }
}
```

`tiqora.api.compat.operations.op_ticket_create` calls
`tiqora.crypto.outbound.apply_email_security()` when an `EmailSecurity`
block is present, which signs and/or encrypts the article body in place
(`EncryptKeys` wins over `SignKey` if both given, matching "encrypt implies
sign" being opt-in via `sign_key_id` passed to the PGP encrypt call). For
S/MIME, `SignKey`/`EncryptKeys` are treated as email addresses resolved via
`SmimeKeyStore`.

**Simplification vs Znuny**: applied to the *stored* article body — Tiqora
has no live outbound-SMTP delivery path for GenericInterface-created
articles yet (see `docs/channels.md`), so this makes the persisted content
reflect sign/encrypt, matching what an operator inspecting the ticket would
see, rather than an actual SMTP envelope. Disabled backends or any crypto
failure leave the body untouched (logged at WARNING/ERROR) — a malformed or
unusable `EmailSecurity` block must never block ticket creation, same as
before this module existed (where it was silently ignored).

## Tests

- `tests/test_crypto_pgp.py` — sign+verify, encrypt+decrypt, tampered
  verify failure, wrong-key decrypt failure, import — against an ephemeral
  gpg keyring generated per test (`GNUPGHOME` under `/tmp`, not pytest's
  `tmp_path`: gpg-agent's Unix socket path has a ~104-byte limit on macOS
  that pytest's nested tmp dirs exceed).
- `tests/test_crypto_smime.py` — same shape against a self-signed
  `openssl req` cert, plus `SmimeKeyStore` register/lookup/path-sanitize.
- `tests/test_crypto_outbound.py` — `EmailSecurity` sign/encrypt applied to
  `ArticleIn.body`, disabled-backend and unknown-key no-op paths.
- `tests/test_crypto_keystore.py` — PGP import records a
  `tiqora_crypto_key` audit row (`@pytest.mark.db`).
- `tests/test_crypto_postmaster.py` (`@pytest.mark.db`) — a PGP clear-signed
  inbound mail creates a ticket/article and gets `article_flag`
  `TiqoraCryptoVerify = "pgp:verified"`.

All crypto tests `pytest.mark.skipif` when `gpg`/`openssl` are not on
`PATH`; both were available in the dev/CI environment used here (Homebrew
`gpg` 2.4.9, `openssl` 3.x on macOS).

## External tool assumptions

- `gpg` binary reachable on `PATH` (or set explicitly via
  `python-gnupg`'s `gpgbinary`, not currently exposed as a Tiqora config
  key — add one if a non-PATH `gpg` is needed).
- `openssl` binary reachable on `PATH` (overridable via
  `TIQORA_CRYPTO_OPENSSL_BIN`).
- Both are subprocess calls; `inbound.py`/`outbound.py` wrap them with
  `asyncio.to_thread()` so they never block the event loop.
