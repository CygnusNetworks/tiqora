# GDPR tools

Two independent tools live under `backend/src/tiqora/gdpr/`:

1. **Customer anonymization** (`tiqora gdpr anonymize-customer`) — scrub PII
   for one customer on request (right to erasure).
2. **Retention policies** (`tiqora gdpr retention-report` /
   `tiqora gdpr retention-run`, plus a feature-flagged worker task) —
   config-driven, scheduled scrubbing of old ticket content.

Both write to Znuny-owned tables (`customer_user`, `customer_company`,
`article_data_mime`) and therefore share a write gate: see
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
(03:00, `gdpr_retention_task` in `tiqora.worker.broker`) but is a no-op
unless the `gdpr.retention.enabled` tiqora_settings key is set to a truthy
value (default OFF — see `tiqora.domain.settings_store`). Flip it via the
existing settings-store helpers (there is no dedicated CLI toggle yet; use
`tiqora gdpr retention-run` for on-demand runs, or set the key directly).

## Tests

`backend/tests/test_gdpr_anonymize.py` and
`backend/tests/test_gdpr_retention.py` cover:

- ownership-gate refusal (no DB needed);
- `--force-parallel` bypass with the warning path exercised;
- `@pytest.mark.db` end-to-end runs (testcontainers MariaDB) verifying PII is
  actually replaced, referential consistency of the mapping, and that
  retention dry-run selects the expected tickets.
