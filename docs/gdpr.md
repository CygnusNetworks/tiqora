# GDPR tools

Three independent tools live under `backend/src/tiqora/gdpr/`:

1. **Customer anonymization** (`tiqora gdpr anonymize-customer`) — scrub PII
   for one customer on request (right to erasure).
2. **Retention policies** (`tiqora gdpr retention-report` /
   `tiqora gdpr retention-run`, plus a feature-flagged worker task) —
   config-driven, scheduled scrubbing of old ticket content.
3. **Admin erasure** (`tiqora.gdpr.erasure`, admin page **Privacy / GDPR**,
   API `/api/v1/admin/gdpr/*`) — selector-based anonymization or hard delete
   of one or many customers, with a preview, a per-row backup and a 30-day
   rollback window. See [Admin erasure](#admin-erasure).

All three write to Znuny-owned tables (`customer_user`, `customer_company`,
`article_data_mime`, …) and therefore share a write gate: see
[Ownership gate](#ownership-gate) below.

## Ownership gate

`tiqora.gdpr.gate.require_write_gate` refuses to write PII changes unless:

- schema-ownership is active (`tiqora ownership status` shows both gates
  passing — see `docs/cutover.md`), **or**
- the caller passes `--force-parallel` (CLI) / `force_parallel=True` (API).

`--force-parallel` is a deliberate override for operators who understand the
risk: anonymizing rows while Znuny may still be running in parallel can
confuse a running Znuny process (stale caches racing an in-flight scrub).
Every use is logged at `WARNING` (`gdpr_force_parallel_write`) and recorded
in `tiqora_gdpr_audit` with `force_parallel=true`.

The worker retention task never passes `--force-parallel` — if ownership is
inactive, it logs `gdpr_retention_refused_ownership_inactive` and no-ops.

The admin erasure API, by contrast, **always** passes `force_parallel=True`
(apply and rollback), so an admin can act during parallel operation; the
override is recorded on the job (`tiqora_gdpr_job.force_parallel`) and in
`tiqora_gdpr_audit`.

## Customer anonymization

```
tiqora gdpr anonymize-customer --login jane.doe@example.com [--seed 42] \
    [--anonymize-company] [--force-parallel] [--actor "operator:alice"]

tiqora gdpr anonymize-customer --customer-id ACME  # every customer_user under ACME
```

Replaces, deterministically (same original value → same replacement,
everywhere it occurs — see `ValueMapper` in
`tiqora.domain.dev_anonymize`, reused rather than duplicated):

- `customer_user`: first/last name, email, login, phone/fax/mobile,
  street/zip/city/country.
- `article_data_mime` for every article on that customer's tickets:
  `a_from`/`a_to`/`a_cc` (email-address occurrences only, rest of the header
  preserved) and `a_body` (lorem-scrubbed, line count and rough line length
  preserved).
- `customer_company.name`, only with `--anonymize-company` (off by default —
  a company may have other, non-anonymized customer_users).

Tickets themselves (title, queue, state, timestamps, dynamic fields) are
**not** touched — they remain intact for analytics/reporting.

Every run writes one `tiqora_gdpr_audit` row: `action=anonymize_customer`,
`target=<login or customer_id:X>`, `actor`, JSON `counts`, `force_parallel`.
The audit row never stores the anonymized values themselves.

## Retention policies

Rules are config-driven, stored as a JSON array in `tiqora_settings` under
key `gdpr.retention.rules`:

```json
[
  {"name": "support-12mo", "queue": "Support", "state_type": "closed", "older_than_months": 12},
  {"name": "sales-24mo", "queue": "Sales", "older_than_months": 24, "seed": 7}
]
```

- `queue` — Znuny queue name to match.
- `state_type` — Znuny `ticket_state_type.name` to match (default
  `"closed"`).
- `older_than_months` — a ticket matches once `ticket.change_time` is older
  than this many months.
- `seed` — optional, per-rule RNG seed for reproducible anonymization.

Unlike customer anonymization, retention operates **per ticket**, not per
customer: it scrubs `article_data_mime` (from/to/cc address occurrences +
body) for matched tickets, leaving `customer_user` untouched (the customer
may have other, non-expired tickets).

```
tiqora gdpr retention-report   # read-only: which tickets each rule would touch
tiqora gdpr retention-run [--force-parallel] [--actor "operator:alice"]
```

Idempotency: each processed ticket gets a `tiqora_gdpr_audit` row
(`action=retention_anonymize`, `target=ticket:<id>`); re-running
`retention-run` skips tickets that already have such a row, so a rule can be
run repeatedly (e.g. daily) without re-scrubbing already-anonymized tickets.

### Worker task

`tiqora.worker.gdpr_retention.run_gdpr_retention_tick` is scheduled daily
(03:00, `_daily_loop("gdpr_retention", ...)` in `tiqora.worker.__main__`) but is a no-op
unless the `gdpr.retention.enabled` tiqora_settings key is set to a truthy
value (default OFF — see `tiqora.domain.settings_store`). Flip it via the
existing settings-store helpers (there is no dedicated CLI toggle yet; use
`tiqora gdpr retention-run` for on-demand runs, or set the key directly).

## Admin erasure

`tiqora.gdpr.erasure` is the engine behind the admin page **Privacy / GDPR**
(`/admin/gdpr`, `frontend/src/routes/admin/GdprPage.tsx`; a bulk action on
the customer-users list opens it with the selected logins pre-filled). It works on a set of
customers selected by filter, offers two modes, and snapshots every row it
changes so a job can be rolled back for 30 days.

### Permissions

Every `/api/v1/admin/gdpr/*` route depends on `AdminUser`, i.e. the caller
must have `rw` on the Znuny `admin` group (directly or via a role). There
is no finer-grained
permission and no CLI for this tool.

### Selecting customers

The selector (`ErasureSelector`) is an **AND** of all given criteria; an
empty selector matches nothing (it never means "everyone"):

- `logins`, `customer_ids` — exact lists.
- `login_regex`, `customer_id_regex`, `email_regex` — Python regexes, each
  with a `*_negate` flag ("must not match"). Patterns longer than 200
  characters or with nested/stacked quantifiers are rejected (ReDoS guard).
- `changed_before` / `changed_after` — on `customer_user.change_time`.
- `valid_id` — e.g. only already-invalid customers.
- `activity` — `no_tickets`, `no_open_tickets`, or
  `inactive_since:YYYY-MM-DD` (no ticket with `change_time` on or after that
  date).

### Modes

**`anonymize`** (default) — replaces values in place, deterministically via
`ValueMapper` (optional `seed`):

- `customer_user`: the vanilla PII columns (login, email, pw, title,
  first/last name, phone/fax/mobile, street/zip/city/country, comments),
  **plus every other column on the live table** except `id`, `customer_id`,
  `valid_id` and the create/change stamps (site-specific extra columns are
  picked up automatically). `pw` is set to NULL, `valid_id` to 2 (invalid).
  Additional columns can be forced via the `gdpr.customer_extra_pii_columns`
  setting (JSON list). Replacements are truncated to the column length and
  never collide with existing unique values.
- `customer_preferences`, `customer_user_customer`, `group_customer_user`:
  `user_id` follows the new login.
- `customer_company` of those customers: name, street, zip, city, country,
  url, comments (the `customer_id` key is kept).
- Tickets whose `customer_user_id` is one of the logins:
  `customer_user_id` follows the new login, `title` becomes
  `[anonymisiert]`; `ticket_history.name` and `dynamic_field_value.value_text`
  (Ticket- and Article-object fields) are set to `[anonymisiert]`.
- Articles on those tickets: `article_data_mime` (from/to/cc/bcc/reply-to
  addresses incl. display names, subject, body, message-id/in-reply-to/
  references), `article_data_mime_plain.body` (raw MIME, scrubbed with
  headers/structure preserved), `article_data_mime_attachment` (filename
  replaced, content emptied, size 0) and `article_search_index`.
- `tiqora_telegram_message` rows for those articles are deleted.

**`delete`** — hard-deletes the customer *master* rows (`customer_user`,
`customer_preferences`, `customer_user_customer`, `group_customer_user`) and
the `customer_company` if no other customer_user remains in it (otherwise the
company is anonymized as above). Tickets are kept: `customer_user_id` and
`customer_id` are replaced by stable tokens, and articles are scrubbed as in
anonymize mode. Ticket titles, `ticket_history` and dynamic-field values are
**not** scrubbed in this mode.

**`delete` + `delete_tickets`** — additionally hard-deletes the customers'
tickets and every FK-linked row (articles and their mime/plain/attachment
data, flags, `mail_queue`, `tiqora_telegram_message`, `article_search_index`,
`time_accounting`, `ticket_history`, watchers, index rows, calendar links,
dynamic-field values) in FK-safe order. Tables missing on an install are
skipped.

Every mode invalidates the Znuny customer-user/-company caches and the
affected tickets' caches.

### Workflow

1. **Preview** — `POST /gdpr/preview` resolves the selector and returns the
   matched customers, per-table counts, sample rows, the columns that would
   change and the tables that would be deleted from. `POST
   /gdpr/selector-count` is the cheap live counter behind the filter form;
   `POST /gdpr/record-preview` shows a before/after diff for one login
   (read-only, the session is always rolled back).
2. **Apply** — `POST /gdpr/jobs` with `confirm: true` (otherwise 422) and
   the explicit `customer_user_ids` from the preview — the selector is **not**
   re-resolved, so customers created after the preview are not included. The
   UI additionally requires typing `LÖSCHEN` for delete mode. All changes and
   their backups run in one transaction; the job is stored in
   `tiqora_gdpr_job` with `status=applied` and
   `backup_expires_at = now + 30 days`.
3. **Backup** — before each mutation the original values go to
   `tiqora_gdpr_backup` (changed columns only for anonymize; the full row for
   anything deleted; binary values base64-wrapped).
   `GET /gdpr/jobs/{id}/backup/download` returns all backup rows of a job as
   JSON (the UI disables it once purged — the backup rows are gone). `GET /gdpr/jobs` (filterable by
   status, mode, login, date range) and `GET /gdpr/jobs/{id}` list jobs.
4. **Rollback** — `POST /gdpr/jobs/{id}/rollback` restores every backup row
   (UPDATE back, or re-INSERT for deleted rows, including hard-deleted
   tickets) and sets `status=rolled_back`. Only jobs in `applied` can be
   rolled back.
5. **Purge** — backups are deleted either manually
   (`POST /gdpr/jobs/{id}/purge-backup`, allowed for `applied` and
   `rolled_back` jobs) or by the daily worker once `backup_expires_at` has
   passed. Either way the job becomes `status=purged` and can no longer be
   rolled back — the erasure is then final.

Each apply, rollback and manual purge writes a `tiqora_gdpr_audit` row
(`action=erasure_anonymize` / `erasure_delete` / `erasure_rollback` /
`erasure_purge`, `target=job:<id>`, `actor=admin:<login>`).

### Backup retention and settings

- Retention is fixed at 30 days (`BACKUP_RETENTION_DAYS`); there is no
  setting for it.
- `tiqora.worker.gdpr_erasure_purge.run_gdpr_erasure_purge_tick` runs daily
  at 03:30 (`_daily_loop("gdpr_erasure_purge", ...)` in
  `tiqora.worker.__main__`). It is gated by `gdpr.erasure.purge_enabled`,
  **default ON** (toggleable like the other services on the admin
  services page), and needs no ownership gate.
- `gdpr.customer_extra_pii_columns` — optional JSON list of extra
  `customer_user` columns to treat as PII.

### Caveats

- **Parallel operation with Znuny.** The admin API bypasses the ownership
  gate (see above). Tiqora invalidates Znuny's caches, but a running Znuny
  process can still hold stale data or write concurrently while a job runs.
- **Backups are PII.** Until purged, `tiqora_gdpr_backup` holds the original
  values in clear text, and the backup download exposes them. Purge early
  (manual purge) if the 30-day window is not needed. The job row keeps the
  selector as entered (e.g. login lists or regexes) after purge; for delete
  jobs `resolved_logins` holds the original logins, for anonymize jobs the
  new ones.
- **Search index follows via the outbox.** Apply and rollback write one
  internal `TiqoraSearchReindex` outbox event per affected ticket in the same
  transaction; the outbox drain (`tiqora-worker`) then re-indexes the ticket,
  or deletes its document when the ticket was hard-deleted. The event is not
  forwarded to webhooks or SSE. Until the drain runs (every 60 s by default,
  `TIQORA_OUTBOX_DRAIN_INTERVAL`; if Meilisearch is down the rows stay
  unprocessed and are retried) search can still show pre-erasure content.
  Jobs applied before this change are not covered: run
  `tiqora index rebuild` for them, and note that `rebuild` only upserts, so
  documents of tickets hard-deleted by such an old job must be removed from
  the index by hand.
- **Scope is by ticket customer.** Only tickets whose `customer_user_id` is
  one of the selected logins are touched. The customer's address or name in
  other customers' tickets is not scrubbed, and Tiqora-owned tables other
  than `tiqora_telegram_message` (e.g. AI drafts/audit, mail log, Telegram
  contacts) are not touched.
- **Anonymize rollback of related tables** recomputes the replacement login
  from the job's `seed`, so it depends on `ValueMapper` producing the same
  output as at apply time.

## Tests

`backend/tests/test_gdpr_anonymize.py` and
`backend/tests/test_gdpr_retention.py` cover:

- ownership-gate refusal (no DB needed);
- `--force-parallel` bypass with the warning path exercised;
- `@pytest.mark.db` end-to-end runs (testcontainers MariaDB) verifying PII is
  actually replaced, referential consistency of the mapping, and that
  retention dry-run selects the expected tickets.

`backend/tests/test_gdpr_erasure.py` covers the admin erasure: selector
resolution (regex semantics, negation, ReDoS guard, `inactive_since`),
anonymize and delete (incl. `delete_tickets`) end-to-end with rollback,
expiry purge blocking rollback, manual purge, extra-PII columns, the
per-customer record preview and selector-count endpoints, and the
structure-preserving MIME/address scrubbers. The admin page has
`frontend/src/routes/admin/GdprPage.test.tsx`.
