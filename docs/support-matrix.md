# Support matrix — OTRS / Znuny 6.0–7.3

Tiqora is **database-compatible** with **OTRS 6.0.x** and **Znuny 6.0–7.3**
(MariaDB/MySQL and PostgreSQL). A runtime **schema profile** detects the peer
shape at startup and adapts groups table name, optional mail OAuth columns,
and 7.0+ state/priority colour defaults.

**Support floor is OTRS / Znuny 6.0.** That includes the last official
((OTRS)) Community Edition 6.0.x (OTRS AG, EOL 2021) and the maintained
**Centuran ((OTRS)) CE 6.0.x** line (same 6.0 schema → profile
`otrs-znuny-6.0`). Peers older than 6.0 are **not** supported for parallel-op;
upgrade them to 6.0+ first (see [Out of scope](#out-of-scope-pre-60-and-other-forks)).

**Preferred production path** remains: upgrade the peer to **Znuny 6.5 LTS**
or **7.3 LTS**, then run parallel-op. Multi-version support is for sites that
cannot upgrade immediately.

Implementation: `tiqora.db.legacy.profile` (`SchemaProfileId`, detection gate).
Operator invariants: [parallel-operation.md](parallel-operation.md).  
GenericInterface wire surface: [compatibility.md](compatibility.md).  
Test layers: [testing.md](testing.md).

---

## Peer product × profile

| Peer product | Profile id | Parallel-op | TiqoraSync Framework | Decisive schema markers |
|--------------|------------|-------------|----------------------|-------------------------|
| OTRS 6.0.x / Centuran CE 6.0.x / Znuny 6.0 | `otrs-znuny-6.0` | Yes | `6.0.x` | Table **`groups`** (not `permission_groups`); no mail OAuth cols |
| Znuny 6.1 | `znuny-6.1` | Yes | `6.1.x` | `permission_groups`; no mail OAuth |
| Znuny 6.2 | `znuny-6.2` | Yes | `6.2.x` | + `acl_ticket_attribute_relations` |
| Znuny 6.3 | `znuny-6.3` | Yes | `6.3.x` | + `mail_account` OAuth columns |
| Znuny 6.4 | `znuny-6.4` | Yes | `6.4.x` | + `mention`, `smime_keys` (fresh DDL often detects as 6.5) |
| Znuny 6.5 | `znuny-6.5` | Yes | `6.5.x` | Baseline ORM / fresh-install bootstrap |
| Znuny 7.0 | `znuny-7.0` | Yes | `7.0.x` | `ticket_state.color` / `ticket_priority.color` NOT NULL |
| Znuny 7.1 | `znuny-7.1` | Yes | `7.1.x` | Surrogate `id` on junction tables (e.g. `group_user`) |
| Znuny 7.2 | `znuny-7.2` | Yes | `7.2.x` | + `article_color`, … |
| Znuny 7.3 | `znuny-7.3` | Yes | `7.3.x` | + `sendmail_config` |

**OTRS 6.0 lineage (same profile):** official ((OTRS)) Community Edition
6.0.x, **Centuran** ((OTRS)) CE 6.0.x (e.g. 6.0.41), and Znuny 6.0.x share the
6.0 schema shape. Detection is schema-based (`groups` table), not product brand.

Profile ids are **version keys** (never letter tiers A–I). Override only for
tests/ops: `TIQORA_LEGACY_SCHEMA_PROFILE=<profile_id>`.

---

## Out of scope: pre-6.0 and other forks

| Product | Parallel-op | Guidance |
|---------|-------------|----------|
| **OTRS 5.x** (and older 4.x / 3.x) | **No** | Major schema break (e.g. article model rewrite on the official 5→6 path). Run upstream **DBUpdate / upgrade to OTRS or Znuny 6.0+** first, then attach Tiqora. Tiqora does **not** replace the 5→6 database upgrade. |
| **OTOBO** (OTRS 6 CE fork, own release line) | **No** (default) | Schema is OTRS-6-derived but diverges. Not part of the supported matrix; evaluate only on explicit request (own profile + golden peer). |
| **OTRS commercial** (proprietary 7/8+) | **No** | Different product; out of open-source peer scope. |
| Heavily custom OPM / unknown DDL | **No** unless override | Unknown profile → startup refuse; see gate docs in [parallel-operation.md](parallel-operation.md). |

**Migration message for OTRS ≤5 sites:** upgrade the peer along the official
chain to **at least 6.0** (preferably **Znuny 6.5 or 7.3 LTS**), then start
parallel-op per [guide/znuny-to-tiqora.md](guide/znuny-to-tiqora.md).

---

## Database engines

| Engine | Schema matrix (Layer A: real DDL + Tiqora) | Peer golden (Layer B: real app container) |
|--------|--------------------------------------------|-------------------------------------------|
| **MariaDB / MySQL** | Yes — all profile fixtures × detect / migrate / R/W | Yes — multi-peer toolkit (`GOLDEN_PEER`, manual) |
| **PostgreSQL** | Yes — same profiles (schema evolution matches MySQL) | Optional later (Znuny-on-PG peer containers are rare) |

Layer A CI: `.github/workflows/schema-matrix.yml` (release tags, nightly,
`workflow_dispatch`). Layer B: `.github/workflows/golden.yml` +
`just golden-all-peers` (manual only).

---

## What is supported vs deferred

| Area | Status |
|------|--------|
| Shared DB parallel-op (additive `tiqora_*` only) | Supported on all profiles above |
| Ticket create / history / TN / escalation write path | Supported; golden-validated on real peers 6.0–7.3 (MariaDB) |
| Groups table (`groups` vs `permission_groups`) | Auto-adapted at startup |
| Mail account OAuth columns (6.3+) | Load-only adapter on 6.0–6.2 |
| State/priority **color** (7.0+) | Admin creates inject default `#FFFFFF` |
| TiqoraSync OPM cache invalidation | Multi-Framework `6.0.x`–`7.3.x` |
| Fresh-install bootstrap (`tiqora bootstrap`) | Still seeds **Znuny 6.5** base DDL (standalone greenfield) |
| Schema ownership / `versions_owned/` | Only after cutover — not multi-version parallel-op |
| Heavily custom OPM tables | Unknown profile → startup refuse (or explicit override) |
| OTRS ≤5.x / OTOBO / commercial OTRS | Out of scope — see [above](#out-of-scope-pre-60-and-other-forks) |

---

## Validation evidence (as implemented)

| Layer | What | Coverage |
|-------|------|----------|
| **A — DDL matrix** | Fixtures under `backend/tests/fixtures/legacy-schema/` | Release anchors × MariaDB + PostgreSQL; full set with `SCHEMA_MATRIX_FULL=1` |
| **B — Peer golden** | Real OTRS/Znuny container + Tiqora on same MariaDB | Peers in `tests/golden/peers.yaml` (6.0.45 … 7.3.5); `just golden-all-peers` |
| **Unit / gate** | Profile classify, groups SQL ban, color default | `backend/tests/test_legacy_schema_profile.py`, `test_groups_sql_ban.py` |

---

## Operator checklist

1. Point Tiqora `DATABASE_URL` at the peer DB (read-only first).
2. Confirm Admin → System info shows the expected **schema profile**.
3. Install TiqoraSync for the peer Framework series
   ([install README](../packages/znuny-addon/TiqoraSync/install/README.md)).
4. Enable writes only after smoke checks
   ([guide/znuny-to-tiqora.md](guide/znuny-to-tiqora.md)).
5. Prefer upgrading stuck peers to **6.5** or **7.3 LTS** when possible.
